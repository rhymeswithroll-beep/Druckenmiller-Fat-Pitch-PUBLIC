"""Earnings Call NLP — sentiment analysis of 8-K filings from SEC EDGAR.
Fetches recent 8-K filings, extracts text, runs VADER + financial lexicon, produces 0-100 score."""
import json, re, ssl, time
from datetime import date, timedelta
import requests
from bs4 import BeautifulSoup
from tools.db import init_db, query, upsert_many

EDGAR_HEADERS = {"User-Agent": "DruckenmillerAlpha/1.0 research@example.com"}
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
REQUEST_DELAY, MAX_FILINGS, LOOKBACK_DAYS = 0.15, 200, 30
HEDGING_WORDS = ["uncertain", "challenging", "headwinds", "volatile", "cautious", "difficult", "risk", "weakness", "pressure", "concern", "worried", "downturn", "slowdown", "deteriorating"]
CONFIDENCE_WORDS = ["confident", "strong", "momentum", "robust", "accelerating", "visibility", "optimistic", "outperform", "record", "exceptional", "exceeded", "beat", "upside", "tailwind"]
GUIDANCE_WORDS = ["guidance", "outlook", "expect", "forecast", "anticipate", "project", "target", "reiterate", "raise", "lower", "withdraw"]

def _get_vader():
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    try: return SentimentIntensityAnalyzer()
    except LookupError: pass
    try:
        import nltk; nltk.download("vader_lexicon", quiet=True); return SentimentIntensityAnalyzer()
    except Exception: pass
    try:
        import nltk
        _ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE
        old = getattr(ssl, "_create_default_https_context", None)
        ssl._create_default_https_context = lambda: _ctx
        try: nltk.download("vader_lexicon", quiet=True)
        finally:
            if old is not None: ssl._create_default_https_context = old
        return SentimentIntensityAnalyzer()
    except Exception as e: print(f"  WARNING: Could not load VADER: {e}"); return None

def _fetch_cik_to_ticker():
    try:
        resp = requests.get(EDGAR_TICKERS_URL, headers=EDGAR_HEADERS, timeout=15); resp.raise_for_status()
        return {int(e["cik_str"]): e["ticker"].upper() for e in resp.json().values()}
    except Exception as e: print(f"  WARNING: Could not fetch CIK-ticker map: {e}"); return {}

def _fetch_recent_filings(start_date, end_date):
    try:
        resp = requests.get(EDGAR_SEARCH_URL, params={"q": '"earnings"', "forms": "8-K", "dateRange": "custom", "startdt": start_date, "enddt": end_date}, headers=EDGAR_HEADERS, timeout=30)
        resp.raise_for_status(); return resp.json().get("hits", {}).get("hits", [])
    except Exception as e: print(f"  WARNING: EDGAR search failed: {e}"); return []

def _extract_text_from_filing(url):
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=20); resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # If this is a filing index page (-index.htm), follow the first EX-99 or .htm document link
        if "-index.htm" in url:
            base_url = url.rsplit("/", 1)[0]
            # Look for EX-99.1, EX-99, or first .htm/.html document link
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.lower().endswith((".htm", ".html")) and "index" not in href.lower():
                    doc_url = href if href.startswith("http") else f"https://www.sec.gov{href}"
                    time.sleep(REQUEST_DELAY)
                    try:
                        doc_resp = requests.get(doc_url, headers=EDGAR_HEADERS, timeout=20)
                        doc_resp.raise_for_status()
                        doc_soup = BeautifulSoup(doc_resp.text, "html.parser")
                        for tag in doc_soup(["script", "style"]): tag.decompose()
                        text = re.sub(r"\s+", " ", doc_soup.get_text(separator=" ", strip=True))
                        if len(text) > 200:
                            return text
                    except Exception:
                        pass
        for tag in soup(["script", "style"]): tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))
    except Exception as e: print(f"    Could not extract filing text: {e}"); return None

def _analyze_text(text, vader):
    words = text.lower().split()
    if not words: return None
    total = len(words)
    vader_compound = 0.0
    if vader is not None:
        chunks = [text[i:i+5000] for i in range(0, min(len(text), 50000), 5000)]
        compounds = [vader.polarity_scores(c)["compound"] for c in chunks]
        vader_compound = sum(compounds) / len(compounds) if compounds else 0.0
    hc = sum(1 for w in words if w in HEDGING_WORDS)
    cc = sum(1 for w in words if w in CONFIDENCE_WORDS)
    gc = sum(1 for w in words if w in GUIDANCE_WORDS)
    return {"word_count": total, "vader_compound": round(vader_compound, 4),
        "hedging_count": hc, "confidence_count": cc, "guidance_count": gc,
        "hedging_ratio": round(hc / total, 6), "confidence_ratio": round(cc / total, 6),
        "guidance_score": round(min(100, gc / total * 10000), 2)}

def _compute_score(metrics, sentiment_delta=None, hedging_delta=None):
    s = (metrics["vader_compound"] + 1) * 25  # 0-50
    s += max(0, 25 - (metrics["hedging_ratio"] * 5000))  # hedging penalty
    s += min(25, metrics["confidence_ratio"] * 5000)  # confidence boost
    if sentiment_delta is not None: s += max(-10, min(10, sentiment_delta * 20))
    return round(max(0, min(100, s)), 2)

def _get_cik_and_ticker(src, cik_to_ticker, universe):
    """Extract CIK and ticker from EDGAR search result _source.
    Handles both old API (entity_id/accession_no) and new API (ciks/adsh).
    Returns (cik_str, ticker) or (None, None) if no match."""
    ticker = None
    cik_str = None

    # New API format: ciks is a list of CIK strings like ['0000715957']
    ciks = src.get("ciks", [])
    if ciks:
        try:
            cik_int = int(ciks[0])
            cik_str = ciks[0].lstrip("0") or "0"
            ticker = cik_to_ticker.get(cik_int)
        except (ValueError, TypeError):
            pass

    # Old API format: entity_id is a single CIK string
    if not ticker:
        eid = src.get("entity_id")
        if eid is not None:
            try:
                cik_int = int(eid)
                cik_str = str(cik_int)
                ticker = cik_to_ticker.get(cik_int)
            except (ValueError, TypeError):
                pass

    # Fallback: parse ticker directly from display_names field
    # Format: 'COMPANY NAME  (TICK)  (CIK XXXXXXX)' or 'COMPANY  (TICK, TICK-PA)  (CIK ...)'
    if not ticker or ticker not in universe:
        display_names = src.get("display_names", [])
        if display_names:
            name = display_names[0] if isinstance(display_names, list) else str(display_names)
            m = re.search(r'\(([A-Z]{1,5})[,\) ]', name)
            if m:
                candidate = m.group(1)
                if candidate in universe:
                    ticker = candidate

    return cik_str, ticker


def _get_filing_url(src, cik_str):
    """Build an EDGAR filing URL from _source dict and CIK string.
    Handles both old (accession_no/primary_doc) and new (adsh) API formats."""
    # New API: adsh field
    acc = src.get("adsh", "") or src.get("accession_no", "")
    if not acc:
        return None
    acc_clean = acc.replace("-", "")

    # New API: cik from ciks list; old API: entity_id
    if not cik_str:
        ciks = src.get("ciks", [])
        cik_str = ciks[0].lstrip("0") if ciks else src.get("entity_id", "")

    primary_doc = src.get("primary_doc", "")
    if primary_doc and cik_str:
        return f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{acc_clean}/{primary_doc}"

    # Use the filing index page — BeautifulSoup will find the EX-99 document link
    if cik_str:
        return f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{acc_clean}/{acc}-index.htm"

    return None

def _infer_quarter(filing_date_str):
    try:
        parts = filing_date_str.split("-"); return f"{parts[0]}Q{(int(parts[1]) - 1) // 3 + 1}"
    except Exception: return f"{date.today().year}Q{(date.today().month - 1) // 3 + 1}"

def run(gated_symbols=None):
    init_db()
    print("=" * 60 + "\nEARNINGS NLP — Sentiment Analysis of 8-K Filings\n" + "=" * 60)
    universe = {r["symbol"] for r in query("SELECT symbol FROM stock_universe")}
    if gated_symbols: universe = universe & set(gated_symbols)
    if not universe: print("  No symbols. Skipping."); return
    print(f"  Universe: {len(universe)} symbols\n  Fetching CIK-to-ticker mapping...")
    cik_to_ticker = _fetch_cik_to_ticker()
    if not cik_to_ticker: print("  Could not load ticker mapping. Aborting."); return
    print(f"  Loaded {len(cik_to_ticker)} CIK mappings"); time.sleep(REQUEST_DELAY)
    vader = _get_vader()
    if vader is None: print("  WARNING: VADER unavailable; sentiment scores will be 0.")
    end_dt = date.today().isoformat()
    start_dt = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f"  Searching EDGAR for 8-K filings ({start_dt} to {end_dt})...")
    hits = _fetch_recent_filings(start_dt, end_dt)
    print(f"  Found {len(hits)} filing hits")
    if not hits: print("  No filings found."); return
    filings, seen = [], set()
    for hit in hits:
        src = hit.get("_source", {})
        cik_str, ticker = _get_cik_and_ticker(src, cik_to_ticker, universe)
        if ticker is None or ticker not in universe or ticker in seen: continue
        seen.add(ticker)
        url = _get_filing_url(src, cik_str)
        if url: filings.append({"symbol": ticker, "filing_url": url, "filing_date": src.get("file_date", date.today().isoformat())})
        if len(filings) >= MAX_FILINGS: break
    print(f"  Matched {len(filings)} filings to universe")
    if not filings: print("  No matching filings."); return
    prior_data = {}
    try:
        for r in query("SELECT symbol, sentiment, hedging_ratio FROM earnings_transcripts"):
            if r["symbol"] not in prior_data: prior_data[r["symbol"]] = r
    except Exception: pass
    transcript_rows, score_rows, processed, errors = [], [], 0, 0
    for f in filings:
        sym, url, fdate = f["symbol"], f["filing_url"], f["filing_date"]
        quarter = _infer_quarter(fdate)
        print(f"  [{processed + 1}/{len(filings)}] {sym} ({quarter})...", end="")
        text = _extract_text_from_filing(url)
        if not text or len(text) < 200: print(" skipped (too short)"); errors += 1; continue
        metrics = _analyze_text(text, vader)
        if metrics is None: print(" skipped (analysis failed)"); errors += 1; continue
        sd, hd = None, None
        prior = prior_data.get(sym)
        if prior and prior.get("sentiment") is not None:
            sd = metrics["vader_compound"] - prior["sentiment"]
            if prior.get("hedging_ratio") is not None: hd = metrics["hedging_ratio"] - prior["hedging_ratio"]
        score = _compute_score(metrics, sd, hd)
        print(f" score={score:.1f}, sentiment={metrics['vader_compound']:+.3f}")
        today_str = date.today().isoformat()
        kp = json.dumps({"hedging_count": metrics["hedging_count"], "confidence_count": metrics["confidence_count"], "guidance_count": metrics["guidance_count"]})
        transcript_rows.append((sym, today_str, quarter, url, metrics["word_count"], metrics["vader_compound"], metrics["hedging_ratio"], metrics["confidence_ratio"], kp))
        details = json.dumps({"vader_compound": metrics["vader_compound"], "hedging_ratio": metrics["hedging_ratio"], "confidence_ratio": metrics["confidence_ratio"], "word_count": metrics["word_count"], "guidance_score": metrics["guidance_score"]})
        score_rows.append((sym, today_str, score, round(sd, 4) if sd is not None else None, round(hd, 6) if hd is not None else None, metrics["guidance_score"], details))
        processed += 1
    if transcript_rows:
        upsert_many("earnings_transcripts", ["symbol", "date", "quarter", "filing_url", "word_count", "sentiment", "hedging_ratio", "confidence_ratio", "key_phrases"], transcript_rows)
    if score_rows:
        upsert_many("earnings_nlp_scores", ["symbol", "date", "earnings_nlp_score", "sentiment_delta", "hedging_delta", "guidance_score", "details"], score_rows)
    print(f"\n  Processed: {processed} | Errors: {errors} | Stored: {len(score_rows)} scores\n  Earnings NLP complete.")
    return {"processed": processed, "scored": len(score_rows), "errors": errors}

if __name__ == "__main__":
    run()
