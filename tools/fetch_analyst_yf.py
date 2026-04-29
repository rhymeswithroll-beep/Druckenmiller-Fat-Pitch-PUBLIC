"""Analyst data via yfinance — replacement for FMP analyst endpoints (deprecated).

Fetches analyst price targets and recommendation distributions for all universe
symbols. Writes to fmp_analyst_data table (same schema as FMP used) so that
analyst_intel.py and downstream scoring modules need zero changes.

Data sources:
  - yf.Ticker.analyst_price_targets  → {current, high, low, mean, median}
  - yf.Ticker.recommendations_summary → strongBuy/buy/hold/sell/strongSell by period

Pipeline: Phase 1.6b (runs after FMP v2 probe fails / alongside it)
"""

import logging
import time
from datetime import date

import yfinance as yf

from tools.db import query, upsert_many, get_conn

logger = logging.getLogger(__name__)

BATCH_DELAY = 0.2   # 5 req/sec — polite ceiling for yfinance


def _ensure_tables():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fmp_analyst_data (
            symbol TEXT, date TEXT,
            analyst_count INTEGER, strong_buy INTEGER, buy INTEGER, hold INTEGER,
            sell INTEGER, strong_sell INTEGER, consensus TEXT,
            price_target REAL, price_target_high REAL, price_target_low REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.commit()
    conn.close()


def _derive_consensus(strong_buy, buy, hold, sell, strong_sell, total):
    """Convert recommendation distribution to a consensus label."""
    if not total:
        return "Hold"
    weighted = (
        (strong_buy or 0) * 5 +
        (buy or 0) * 4 +
        (hold or 0) * 3 +
        (sell or 0) * 2 +
        (strong_sell or 0) * 1
    )
    score = weighted / (total * 5) * 100
    if score >= 75:
        return "Strong Buy"
    elif score >= 60:
        return "Buy"
    elif score >= 40:
        return "Hold"
    elif score >= 25:
        return "Sell"
    return "Strong Sell"


def _fetch_symbol(sym: str) -> tuple | None:
    """Fetch analyst data for a single symbol. Returns DB row tuple or None."""
    try:
        t = yf.Ticker(sym)

        # ── Price targets ──────────────────────────────────────────────
        pt_mean = pt_high = pt_low = None
        try:
            pts = t.analyst_price_targets
            if pts and isinstance(pts, dict):
                pt_mean = pts.get("mean") or pts.get("current")
                pt_high = pts.get("high")
                pt_low = pts.get("low")
        except Exception:
            pass

        # ── Recommendation distribution ────────────────────────────────
        strong_buy = buy = hold = sell = strong_sell = 0
        try:
            recs = t.recommendations_summary
            if recs is not None and not recs.empty:
                # Period '0m' = current month; fall back to first row if missing
                if "0m" in recs.index:
                    row = recs.loc["0m"]
                else:
                    row = recs.iloc[0]
                strong_buy  = int(row.get("strongBuy", 0)  or 0)
                buy         = int(row.get("buy", 0)        or 0)
                hold        = int(row.get("hold", 0)       or 0)
                sell        = int(row.get("sell", 0)       or 0)
                strong_sell = int(row.get("strongSell", 0) or 0)
        except Exception:
            pass

        total = strong_buy + buy + hold + sell + strong_sell

        # Skip symbols with zero data from both sources
        if total == 0 and pt_mean is None:
            return None

        consensus = _derive_consensus(strong_buy, buy, hold, sell, strong_sell, total)

        return (
            sym, date.today().isoformat(),
            total, strong_buy, buy, hold, sell, strong_sell,
            consensus, pt_mean, pt_high, pt_low,
        )

    except Exception as e:
        logger.debug(f"yfinance analyst fetch failed for {sym}: {e}")
        return None


def run():
    """Fetch analyst data for all universe symbols via yfinance."""
    _ensure_tables()

    symbols = [r["symbol"] for r in query("SELECT symbol FROM stock_universe")]
    if not symbols:
        print("  No symbols in universe — skipping analyst yfinance fetch")
        return

    print(f"  Fetching yfinance analyst data for {len(symbols)} symbols...")

    rows = []
    skipped = 0

    for i, sym in enumerate(symbols):
        row = _fetch_symbol(sym)
        if row:
            rows.append(row)
        else:
            skipped += 1

        # Progress and pacing
        if (i + 1) % 100 == 0:
            print(f"    Progress: {i + 1}/{len(symbols)} — {len(rows)} with data")
        time.sleep(BATCH_DELAY)

    if rows:
        upsert_many(
            "fmp_analyst_data",
            ["symbol", "date", "analyst_count", "strong_buy", "buy", "hold",
             "sell", "strong_sell", "consensus", "price_target",
             "price_target_high", "price_target_low"],
            rows,
        )

    strong_buy_count = sum(1 for r in rows if r[8] == "Strong Buy")
    print(f"  Analyst yfinance: {len(rows)} symbols with data, "
          f"{skipped} no data, {strong_buy_count} Strong Buy consensus")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from tools.db import init_db
    init_db()
    run()
