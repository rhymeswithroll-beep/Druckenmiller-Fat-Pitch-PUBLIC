"""Signal generator: combines macro + technical + fundamental into actionable signals.

Druckenmiller principle: "It's not whether you're right or wrong, but how much you
make when you're right and how much you lose when you're wrong."
"""

import numpy as np
import pandas as pd
from datetime import datetime
from tools.config import (
    REGIME_WEIGHTS, SIGNAL_THRESHOLDS, MIN_RR_RATIO, ATR_PERIOD,
)
from tools.db import init_db, upsert_many, query_df


def get_current_regime():
    """Get the latest macro regime and score."""
    df = query_df("SELECT * FROM macro_scores ORDER BY date DESC LIMIT 1")
    if df.empty:
        return "neutral", 50
    row = df.iloc[0]
    return row["regime"], float(row["total_score"])


def normalize_macro_to_100(macro_score):
    """Convert macro score (-100 to +100) to 0-100 scale for blending."""
    return (macro_score + 100) / 2


def compute_atr(price_df, symbol, period=ATR_PERIOD):
    """Compute Average True Range for stop loss calculation."""
    df = price_df[price_df["symbol"] == symbol].sort_values("date").tail(period + 5)
    if len(df) < period:
        return None

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    tr = []
    for i in range(1, len(high)):
        tr.append(max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        ))

    if len(tr) < period:
        return None
    return np.mean(tr[-period:])


def compute_stop_loss(current_price, atr, dma50):
    """Stop loss: tighter of 1.5x ATR or 3% below 50 DMA. Hard cap at 12% max loss.

    Tighter stops force honest R:R — a 30% stop on a volatile stock produces
    a mathematically valid but operationally useless number.
    """
    stops = []
    if atr is not None:
        stops.append(current_price - 1.5 * atr)
    if dma50 is not None and dma50 > 0:
        stops.append(dma50 * 0.97)  # 3% below 50 DMA

    if not stops:
        return current_price * 0.92  # Fallback: 8% stop

    return max(min(stops), current_price * 0.88)  # Hard cap: never more than 12%


def compute_target_and_rr(entry_price, stop_loss, atr, price_df, symbol, analyst_target):
    """Compute target price and R:R using analyst consensus as a dual-purpose anchor.

    The core insight: when a momentum stock runs past analyst consensus, that gap
    is not a data quality problem — it IS the risk. The stock has been levitated
    by momentum past where fundamentals justify the price.

    Effective risk = max(stop_loss_risk, analyst_gap)
      - analyst_gap = entry - analyst_target  (when stock is ABOVE consensus)
      - If analysts still see upside (target > entry), gap = 0, stop dominates

    Target priority:
      1. Analyst consensus target (if above entry — genuine upside they see)
      2. 52-week high (if >3% above entry — prior resistance as natural target)
      3. ATR projection: entry + 4x ATR (momentum extension for breakout stocks)

    Result: R:R is genuinely differentiated —
      - Stock below analyst target + tight stop = high R:R (good asymmetry)
      - Stock above analyst target = effective risk is large = low R:R (honest)
      - No artificial floor: a 0.8 is shown as 0.8, not rounded up to 2.0
    """
    stop_risk = entry_price - stop_loss
    if stop_risk <= 0:
        return round(entry_price * 1.08, 4), 0.0

    # Analyst gap: how far stock has run above analyst consensus (0 if analysts bullish)
    analyst_gap = 0.0
    if analyst_target and analyst_target > 0:
        analyst_gap = max(0.0, entry_price - analyst_target)

    # Effective risk: larger of trading stop risk or fundamental reversion gap
    effective_risk = max(stop_risk, analyst_gap)

    # Upside target
    target = None

    # 1. Analyst consensus — only use if they still see meaningful upside (>3%)
    if analyst_target and analyst_target > entry_price * 1.03:
        target = analyst_target

    # 2. 52-week high — only if meaningfully above entry (>3%)
    if target is None:
        sym_prices = price_df[price_df["symbol"] == symbol].sort_values("date")
        if len(sym_prices) >= 20:
            w52_high = float(sym_prices.tail(252)["high"].max())
            if w52_high > entry_price * 1.03:
                target = w52_high

    # 3. ATR projection: entry + 4x ATR (momentum extension)
    if target is None:
        atr_val = atr if atr is not None else stop_risk / 1.5
        target = entry_price + 4.0 * atr_val

    upside = target - entry_price
    rr = round(upside / effective_risk, 2) if effective_risk > 0 else 0.0

    return round(target, 4), rr


def run():
    """Generate signals for all scored assets."""
    init_db()
    print("Generating signals...")

    regime, macro_total = get_current_regime()
    macro_normalized = normalize_macro_to_100(macro_total)
    weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["neutral"])
    macro_wt, tech_wt, fund_wt = weights

    print(f"  Regime: {regime} ({macro_total:+.0f}) | "
          f"Weights: Macro {macro_wt:.0%} Tech {tech_wt:.0%} Fund {fund_wt:.0%}")

    # Get latest scores
    tech_df = query_df("""
        SELECT t.* FROM technical_scores t
        INNER JOIN (SELECT symbol, MAX(date) as max_date FROM technical_scores GROUP BY symbol) m
        ON t.symbol = m.symbol AND t.date = m.max_date
    """)
    fund_df = query_df("""
        SELECT f.* FROM fundamental_scores f
        INNER JOIN (SELECT symbol, MAX(date) as max_date FROM fundamental_scores GROUP BY symbol) m
        ON f.symbol = m.symbol AND f.date = m.max_date
    """)
    price_df = query_df("SELECT * FROM price_data")

    # Load analyst consensus targets — used as dual-purpose anchor in R:R calc
    analyst_df = query_df(
        "SELECT symbol, value FROM fundamentals WHERE metric = 'analyst_target_consensus'"
    )
    analyst_targets = (
        dict(zip(analyst_df["symbol"], analyst_df["value"]))
        if not analyst_df.empty else {}
    )
    print(f"  Analyst targets loaded: {len(analyst_targets)} symbols")

    if tech_df.empty:
        print("  No technical scores. Run technical_scoring.py first.")
        return

    # Get asset classes
    asset_classes = query_df(
        "SELECT DISTINCT symbol, asset_class FROM price_data"
    ).drop_duplicates(subset="symbol").set_index("symbol")["asset_class"].to_dict()

    today = datetime.now().strftime("%Y-%m-%d")
    results = []

    for _, tech_row in tech_df.iterrows():
        symbol = tech_row["symbol"]
        tech_score = float(tech_row["total_score"])
        asset_class = str(asset_classes.get(symbol) or "stock")

        # Get fundamental score (default 50 for crypto/commodities)
        fund_row = fund_df[fund_df["symbol"] == symbol]
        if not fund_row.empty:
            fund_score = float(fund_row.iloc[0]["total_score"])
        else:
            fund_score = 50.0  # Neutral for crypto/commodities

        # Compute composite
        composite = (macro_normalized * macro_wt +
                     tech_score * tech_wt +
                     fund_score * fund_wt)

        # Classify signal
        if composite >= SIGNAL_THRESHOLDS["strong_buy"]:
            signal = "STRONG BUY"
        elif composite >= SIGNAL_THRESHOLDS["buy"]:
            signal = "BUY"
        elif composite >= SIGNAL_THRESHOLDS["neutral"]:
            signal = "NEUTRAL"
        elif composite >= SIGNAL_THRESHOLDS["sell"]:
            signal = "SELL"
        else:
            signal = "STRONG SELL"

        # Get current price
        sym_prices = price_df[price_df["symbol"] == symbol].sort_values("date")
        if sym_prices.empty:
            continue
        current_price = float(sym_prices.iloc[-1]["close"])

        if current_price <= 0:
            continue

        # Compute entry/stop/target
        entry_price = current_price
        atr = compute_atr(price_df, symbol)

        # 50 DMA
        if len(sym_prices) >= 50:
            dma50 = sym_prices["close"].tail(50).mean()
        else:
            dma50 = None

        stop_loss = compute_stop_loss(current_price, atr, dma50)
        risk = entry_price - stop_loss

        if risk <= 0:
            continue

        target_price, rr_ratio = compute_target_and_rr(
            entry_price, stop_loss, atr, price_df, symbol,
            analyst_targets.get(symbol)
        )

        results.append((
            symbol, today, asset_class,
            round(macro_normalized, 1), round(tech_score, 1), round(fund_score, 1),
            round(composite, 1), signal,
            round(entry_price, 4), round(stop_loss, 4), round(target_price, 4),
            round(rr_ratio, 2),
            None, None,  # Position size calculated separately
        ))

    upsert_many(
        "signals",
        ["symbol", "date", "asset_class", "macro_score", "technical_score",
         "fundamental_score", "composite_score", "signal",
         "entry_price", "stop_loss", "target_price", "rr_ratio",
         "position_size_shares", "position_size_dollars"],
        results
    )

    # Summary
    signal_counts = {}
    for r in results:
        sig = r[7]
        signal_counts[sig] = signal_counts.get(sig, 0) + 1

    print(f"\n  Generated {len(results)} signals:")
    for sig in ["STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"]:
        count = signal_counts.get(sig, 0)
        if count:
            print(f"    {sig:15s}: {count}")

    # Show top STRONG BUY / BUY
    buys = [r for r in results if r[7] in ("STRONG BUY", "BUY")]
    buys.sort(key=lambda x: x[6], reverse=True)
    if buys:
        print(f"\n  Top Buy Signals:")
        for r in buys[:15]:
            print(f"    {r[7]:11s} | {r[0]:12s} ({r[2]:9s}) | "
                  f"Score: {r[6]:5.1f} | Entry: {r[8]:10.2f} | "
                  f"Stop: {r[9]:10.2f} | Target: {r[10]:10.2f} | R:R {r[11]:.1f}")


if __name__ == "__main__":
    run()
