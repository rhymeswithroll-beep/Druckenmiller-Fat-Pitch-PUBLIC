"""SEC EDGAR Form 4 full parser — insider transaction detail extractor.

No API key required. EDGAR rate limit: 10 req/sec.

Pipeline:
  Phase 1.9 — runs in parallel with other fetchers.

Fetches recent Form 4 filings from EDGAR EFTS search, then downloads
and parses the actual XML document to extract:
  - Reporting owner name + title
  - Transaction date, shares, price, acquired/disposed code
  - Shares owned after transaction

Strategy:
  1. Pull company CIK→ticker map from EDGAR's static company_tickers.json
  2. Query EFTS for Form 4 filings in last 7 days (paginated)
  3. Filter hits where a CIK in the hit matches our universe
  4. Fetch the Form 4 XML (filename embedded in EFTS _id field)
  5. Parse nonDerivativeTransaction nodes
  6. Write to edgar_insider_raw + insider_transactions

Writes to:
  - edgar_insider_raw       (raw XML-parsed rows, deduped by accession+owner)
  - edgar_filing_metadata   (filing-level metadata)
  - insider_transactions    (same table insider_trading.py uses for signals)
"""

import logging
import re
import time
import requests
from datetime import date, timedelta
from xml.etree import ElementTree as ET

from tools.config import EDGAR_HEADERS
from tools.db import get_conn, query, upsert_many

logger = logging.getLogger(__name__)

EDGAR_EFTS = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

REQUEST_DELAY = 0.13   # ~7.5 req/sec — safely under EDGAR's 10/sec limit
FILINGS_PER_RUN = 400  # Cap XML fetches per daily run


# ── Table setup ───────────────────────────────────────────────────────

def _ensure_tables():
    """Create edgar tables in SQLite (both are in LOCAL_TABLES)."""
    import sqlite3 as _sqlite3
    import os as _os
    db_path = _os.path.abspath(
        _os.path.join(_os.path.dirname(__file__), "..", ".tmp", "druckenmiller.db")
    )
    _os.makedirs(_os.path.dirname(db_path), exist_ok=True)
    conn = _sqlite3.connect(db_path, timeout=30)
    conn.executescript("""
CREATE TABLE IF NOT EXISTS edgar_insider_raw (
    accession TEXT, owner_name TEXT,
    symbol TEXT, date TEXT, title TEXT,
    transaction_type TEXT, shares REAL, price REAL, value REAL,
    shares_owned_after REAL, form_type TEXT, filing_url TEXT,
    PRIMARY KEY (accession, owner_name)
);
CREATE TABLE IF NOT EXISTS edgar_filing_metadata (
    accession TEXT PRIMARY KEY,
    symbol TEXT, date TEXT, form_type TEXT,
    filer_name TEXT, filing_url TEXT, description TEXT
);
    """)
    conn.commit()
    conn.close()


# ── CIK → ticker map ─────────────────────────────────────────────────

def _build_cik_map(universe_symbols: set) -> dict:
    """
    Fetch EDGAR's company_tickers.json and return {cik_int: ticker} for
    universe symbols only. This lets us pre-filter EFTS hits by CIK before
    fetching XML, avoiding a round-trip per non-universe filing.
    """
    try:
        r = requests.get(EDGAR_TICKERS_URL, headers=EDGAR_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()  # {str_idx: {cik_str, name, ticker}}
        cik_map = {}
        for entry in data.values():
            ticker = (entry.get("ticker") or "").upper()
            cik = entry.get("cik_str")
            if ticker in universe_symbols and cik:
                cik_map[int(cik)] = ticker
        return cik_map
    except Exception as e:
        logger.warning(f"Could not build CIK map: {e}")
        return {}


# ── EDGAR EFTS search ─────────────────────────────────────────────────

def _search_form4(days_back: int = 7, from_offset: int = 0, size: int = 100) -> list:
    """Return Form 4 EFTS hits for the last N days."""
    since = (date.today() - timedelta(days=days_back)).isoformat()
    today_str = date.today().isoformat()
    try:
        r = requests.get(
            EDGAR_EFTS,
            params={
                "forms": "4",
                "dateRange": "custom",
                "startdt": since,
                "enddt": today_str,
                "from": from_offset,
                "size": size,
            },
            headers=EDGAR_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("hits", {}).get("hits", [])
    except Exception as e:
        logger.debug(f"EDGAR EFTS search failed: {e}")
        return []


# ── Parse hit → accession, CIK, XML filename ─────────────────────────

def _parse_hit(hit: dict) -> tuple:
    """
    Extract (accession, cik_str, xml_filename, file_date, entity_name) from
    an EFTS hit.

    EDGAR EFTS response format (as of 2025-26):
      _id  = "{accession}:{xml_filename}"   e.g. "0000313616-26-000122:wk-form4.xml"
      _source.adsh  = accession number       e.g. "0000313616-26-000122"
      _source.ciks  = [insider_cik, company_cik]
      _source.display_names = ["Name (CIK ...)", "Company (CIK ...)"]
      _source.file_date = "2026-04-28"
    """
    src = hit.get("_source", {})
    full_id = hit.get("_id", "")
    adsh = src.get("adsh", "")

    # Split _id on ':' to get xml filename
    xml_filename = ""
    if ":" in full_id:
        xml_filename = full_id.split(":", 1)[1]

    # CIK is the first 10 digits of the accession (zero-padded), then strip leading zeros
    accession_nodash = adsh.replace("-", "")
    cik_str = str(int(accession_nodash[:10])) if len(accession_nodash) >= 10 else ""

    file_date = src.get("file_date", date.today().isoformat())

    # Parse display_names to find entity name (second entry is usually the company)
    display_names = src.get("display_names", [])
    entity_name = display_names[-1] if display_names else ""
    # Strip the "(CIK ...)" suffix
    entity_name = re.sub(r"\s*\(CIK\s+\d+\)\s*$", "", entity_name).strip()

    ciks_raw = src.get("ciks", [])
    cik_ints = [int(c) for c in ciks_raw if c.isdigit()]

    return adsh, accession_nodash, cik_str, xml_filename, file_date, entity_name, cik_ints


# ── XML fetch + parse ─────────────────────────────────────────────────

def _fetch_xml(cik: str, accession_nodash: str, filename: str,
               fallback_ciks: list | None = None) -> str | None:
    """Fetch the raw Form 4 XML content.

    EDGAR sometimes stores filings under the COMPANY's CIK and sometimes
    under the INSIDER's CIK. We try the accession-prefix CIK first, then
    fall back to any other CIKs present in the filing.
    """
    all_ciks = [cik]
    if fallback_ciks:
        for c in fallback_ciks:
            c_str = str(int(c)) if str(c).isdigit() else str(c)
            if c_str not in all_ciks:
                all_ciks.append(c_str)

    for try_cik in all_ciks:
        url = f"{EDGAR_ARCHIVE}/{try_cik}/{accession_nodash}/{filename}"
        try:
            r = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
            if r.status_code == 404:
                continue  # try next CIK
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.debug(f"XML fetch failed ({try_cik}/{accession_nodash}/{filename}): {e}")
            continue
    return None


def _safe_float(text) -> float | None:
    if not text:
        return None
    try:
        return float(str(text).strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _parse_form4_xml(xml_text: str, fallback_symbol: str,
                     accession: str, cik: str, accession_nodash: str) -> list[dict]:
    """Parse Form 4 XML; return list of transaction dicts ready for DB insertion."""
    try:
        # Strip namespace declarations that would break ElementTree
        xml_clean = re.sub(r'\s+xmlns(?::[^=]*)?\s*=\s*"[^"]*"', "", xml_text)
        root = ET.fromstring(xml_clean.encode("utf-8"))
    except ET.ParseError as e:
        logger.debug(f"XML parse error for {accession}: {e}")
        return []

    # Issuer symbol (the company being traded)
    issuer_symbol = (root.findtext(".//issuerTradingSymbol") or fallback_symbol or "").strip().upper()

    # Reporting owner info
    owner_name = (root.findtext(".//reportingOwnerId/rptOwnerName") or "").strip()
    title = (root.findtext(".//reportingOwnerRelationship/officerTitle") or "").strip()
    if not title:
        if root.findtext(".//reportingOwnerRelationship/isDirector") == "1":
            title = "Director"
        elif root.findtext(".//reportingOwnerRelationship/isOfficer") == "1":
            title = "Officer"
        elif root.findtext(".//reportingOwnerRelationship/isTenPercentOwner") == "1":
            title = "10% Owner"

    filing_url = f"{EDGAR_ARCHIVE}/{cik}/{accession_nodash}/"

    results = []
    for tx_node in root.findall(".//nonDerivativeTransaction"):
        # Date
        tx_date = (tx_node.findtext(".//transactionDate/value") or
                   tx_node.findtext(".//transactionDate") or "").strip()[:10]
        if not tx_date or len(tx_date) < 8:
            continue

        # Amounts
        shares_text = (tx_node.findtext(".//transactionAmounts/transactionShares/value") or
                       tx_node.findtext(".//transactionShares/value") or "0")
        price_text  = (tx_node.findtext(".//transactionAmounts/transactionPricePerShare/value") or
                       tx_node.findtext(".//transactionPricePerShare/value") or "0")
        disp_code   = (tx_node.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value") or
                       tx_node.findtext(".//transactionAcquiredDisposedCode/value") or "").strip().upper()
        tx_code     = (tx_node.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
        owned_text  = (tx_node.findtext(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value") or
                       tx_node.findtext(".//sharesOwnedFollowingTransaction/value"))

        # Classify transaction type
        if tx_code == "P" or disp_code == "A":
            tx_type = "BUY"
        elif tx_code in ("S", "S+") or disp_code == "D":
            tx_type = "SELL"
        elif tx_code == "F":
            tx_type = "TAX_WITHHOLDING"
        elif tx_code in ("M", "C"):
            tx_type = "OPTION_EXERCISE"
        elif tx_code in ("A", "G"):
            tx_type = "GRANT"
        else:
            tx_type = "UNKNOWN"

        shares = _safe_float(shares_text) or 0.0
        price  = _safe_float(price_text) or 0.0
        value  = shares * price

        results.append({
            "accession":        accession,
            "owner_name":       owner_name,
            "symbol":           issuer_symbol,
            "date":             tx_date,
            "title":            title,
            "transaction_type": tx_type,
            "shares":           shares,
            "price":            round(price, 4) if price else None,
            "value":            round(value, 2),
            "shares_owned_after": _safe_float(owned_text),
            "filing_url":       filing_url,
        })

    return results


# ── Main run ──────────────────────────────────────────────────────────

def run():
    _ensure_tables()
    today = date.today().isoformat()

    print("  Fetching SEC EDGAR Form 4 filings (full XML parse)...")

    # Universe for filtering
    universe = {r["symbol"] for r in query("SELECT symbol FROM stock_universe")}
    if not universe:
        print("  No symbols in universe — skipping EDGAR fetch")
        return

    # Build CIK→ticker map (single request, pre-filters without fetching XML)
    print("    Building CIK-ticker map from EDGAR...")
    cik_map = _build_cik_map(universe)
    universe_ciks = set(cik_map.keys())
    print(f"    CIK map: {len(cik_map)} universe symbols matched to EDGAR CIKs")

    # Already-processed accessions (7-day window)
    existing_accessions = {r["accession"] for r in query(
        "SELECT accession FROM edgar_filing_metadata WHERE date >= ?",
        [(date.today() - timedelta(days=7)).isoformat()],
    )}

    # Collect EFTS hits (paginate up to FILINGS_PER_RUN)
    all_hits = []
    for offset in range(0, FILINGS_PER_RUN * 4, 100):   # fetch 4x more than cap to allow for filtering
        batch = _search_form4(days_back=7, from_offset=offset, size=100)
        if not batch:
            break
        all_hits.extend(batch)
        time.sleep(REQUEST_DELAY)
        if len(all_hits) >= FILINGS_PER_RUN * 4:
            break

    print(f"    EDGAR EFTS: {len(all_hits)} Form 4 hits in last 7 days")

    # Filter to universe hits by CIK
    universe_hits = []
    for hit in all_hits:
        adsh, accession_nodash, cik_str, xml_filename, file_date, entity_name, cik_ints = _parse_hit(hit)
        if not adsh or adsh in existing_accessions:
            continue
        if not xml_filename:
            continue
        # Check if any CIK in the hit matches our universe
        matched_ciks = universe_ciks.intersection(cik_ints)
        if not matched_ciks:
            continue
        # Use the first matched CIK to get the ticker
        matched_ticker = cik_map.get(next(iter(matched_ciks)), "")
        universe_hits.append((adsh, accession_nodash, cik_str, xml_filename,
                               file_date, entity_name, matched_ticker, cik_ints))
        if len(universe_hits) >= FILINGS_PER_RUN:
            break

    print(f"    Universe-matched new filings to parse: {len(universe_hits)}")

    meta_rows = []
    raw_rows = []
    tx_rows = []

    for adsh, accession_nodash, cik_str, xml_filename, file_date, entity_name, ticker, cik_ints in universe_hits:
        # ── Store metadata ────────────────────────────────────────────
        filing_base_url = f"{EDGAR_ARCHIVE}/{cik_str}/{accession_nodash}/"
        meta_rows.append((
            adsh, ticker, file_date, "4",
            entity_name, filing_base_url, "Form 4 insider transaction",
        ))

        # ── Fetch + parse XML ─────────────────────────────────────────
        time.sleep(REQUEST_DELAY)
        xml_text = _fetch_xml(cik_str, accession_nodash, xml_filename, fallback_ciks=cik_ints)
        if not xml_text:
            continue

        parsed = _parse_form4_xml(xml_text, ticker, adsh, cik_str, accession_nodash)

        for p in parsed:
            # edgar_insider_raw row
            raw_rows.append((
                p["accession"], p["owner_name"],
                p["symbol"] or ticker, p["date"], p["title"],
                p["transaction_type"], p["shares"], p["price"], p["value"],
                p["shares_owned_after"], "4", p["filing_url"],
            ))
            # insider_transactions row (same schema as yfinance/FMP fallback)
            tx_rows.append((
                p["symbol"] or ticker,
                p["date"],
                p["owner_name"],
                p["title"],
                p["transaction_type"],
                int(p["shares"]) if p["shares"] else 0,
                p["price"],
                p["value"],
                p["shares_owned_after"],
                f"edgar://{adsh}/{p['owner_name']}",
                "edgar",
            ))

    # ── Persist ───────────────────────────────────────────────────────
    if meta_rows:
        upsert_many(
            "edgar_filing_metadata",
            ["accession", "symbol", "date", "form_type",
             "filer_name", "filing_url", "description"],
            meta_rows,
        )

    if raw_rows:
        upsert_many(
            "edgar_insider_raw",
            ["accession", "owner_name", "symbol", "date", "title",
             "transaction_type", "shares", "price", "value",
             "shares_owned_after", "form_type", "filing_url"],
            raw_rows,
        )

    if tx_rows:
        upsert_many(
            "insider_transactions",
            ["symbol", "date", "insider_name", "insider_title", "transaction_type",
             "shares", "price", "value", "shares_owned_after", "filing_url", "source"],
            tx_rows,
        )

    buys  = sum(1 for r in tx_rows if r[4] == "BUY")
    sells = sum(1 for r in tx_rows if r[4] == "SELL")
    print(f"  EDGAR Form 4: {len(meta_rows)} filings | "
          f"{len(raw_rows)} transactions ({buys} buys, {sells} sells) "
          f"written to insider_transactions")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from tools.db import init_db
    init_db()
    run()
