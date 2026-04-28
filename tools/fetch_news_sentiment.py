"""Fetch news sentiment from Finnhub and analyst recommendations.

Finnhub provides:
- Company news with per-article sentiment scoring
- Analyst recommendation trends (strong buy/buy/hold/sell counts)
- Earnings calendar (upcoming catalyst dates)
- General market news sentiment
"""

import time
from datetime import datetime, timedelta
import finnhub
from tools.config import FINNHUB_API_KEY
from tools.db import init_db, upsert_many, query, get_conn


def get_client():
    return finnhub.Client(api_key=FINNHUB_API_KEY)


def _vader_sentiment(texts):
    """Run VADER on a list of text strings; return avg compound score [-1, 1]."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        sia = SentimentIntensityAnalyzer()
        scores = [sia.polarity_scores(t)["compound"] for t in texts if t]
        return sum(scores) / len(scores) if scores else 0.0
    except ImportError:
        return 0.0


def fetch_news_sentiment(client, symbols):
    """Fetch company news sentiment scores from Finnhub.

    Uses the premium /news-sentiment endpoint if available; falls back to
    running VADER on /company-news headlines (free tier compatible).
    """
    rows = []
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    # Probe whether premium news_sentiment is available
    _premium_available = None

    for i, symbol in enumerate(symbols):
        try:
            news = client.company_news(symbol, _from=week_ago, to=today)
            if not news:
                continue

            # Try premium endpoint first; cache availability after first probe
            bull_pct = bear_pct = buzz_score = 0.0
            nonlocal_premium = _premium_available

            if nonlocal_premium is not False:
                try:
                    sentiment = client.news_sentiment(symbol)
                    if sentiment and "sentiment" in sentiment:
                        s = sentiment["sentiment"]
                        buz = sentiment.get("buzz", {})
                        bull_pct = float(s.get("bullishPercent", 0))
                        bear_pct = float(s.get("bearishPercent", 0))
                        buzz_score = float(buz.get("buzz", 0))
                        _premium_available = True
                    else:
                        _premium_available = False
                except Exception:
                    _premium_available = False

            # Free-tier fallback: VADER on headlines
            if not _premium_available or nonlocal_premium is False:
                headlines = [a.get("headline", "") for a in news[:20]]
                compound = _vader_sentiment(headlines)
                bull_pct = max(0.0, compound)
                bear_pct = max(0.0, -compound)
                buzz_score = min(len(news) / 10.0, 1.0)

            rows.append((
                symbol, today,
                bear_pct, bull_pct, buzz_score,
                float(len(news)),
                len(news),
            ))
        except Exception:
            pass

        if (i + 1) % 50 == 0:
            print(f"    Sentiment: {i + 1}/{len(symbols)}")
            time.sleep(1)
        elif (i + 1) % 10 == 0:
            time.sleep(0.5)

    return rows


def fetch_analyst_recommendations(client, symbols):
    """Fetch analyst recommendation trends (strong buy/buy/hold/sell counts)."""
    rows = []
    for symbol in symbols:
        try:
            recs = client.recommendation_trends(symbol)
            if not recs:
                continue

            # Get most recent month
            latest = recs[0]
            strong_buy = int(latest.get("strongBuy", 0))
            buy = int(latest.get("buy", 0))
            hold = int(latest.get("hold", 0))
            sell = int(latest.get("sell", 0))
            strong_sell = int(latest.get("strongSell", 0))
            total = strong_buy + buy + hold + sell + strong_sell

            if total == 0:
                continue

            # Store as fundamentals
            bullish = (strong_buy + buy) / total * 100
            bearish = (sell + strong_sell) / total * 100

            rows.extend([
                (symbol, "finnhub_analyst_bullish_pct", bullish),
                (symbol, "finnhub_analyst_bearish_pct", bearish),
                (symbol, "finnhub_analyst_strong_buy", float(strong_buy)),
                (symbol, "finnhub_analyst_total", float(total)),
            ])
        except Exception:
            pass
        time.sleep(0.1)

    return rows


def fetch_earnings_calendar(client):
    """Fetch upcoming earnings dates for stocks in our universe."""
    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    try:
        calendar = client.earnings_calendar(_from=from_date, to=to_date, symbol="", international=False)
        events = calendar.get("earningsCalendar", [])
        rows = []
        for evt in events:
            symbol = evt.get("symbol", "")
            if not symbol:
                continue
            rows.append((
                symbol,
                evt.get("date", ""),
                float(evt.get("epsEstimate") or 0),
                float(evt.get("revenueEstimate") or 0),
            ))
        return rows
    except Exception:
        return []


def run():
    """Fetch all news/sentiment data."""
    init_db()

    if not FINNHUB_API_KEY:
        print("  ERROR: FINNHUB_API_KEY not set in .env")
        return

    client = get_client()

    # Get stock symbols
    symbols = [r["symbol"] for r in query("SELECT symbol FROM stock_universe LIMIT 500")]
    if not symbols:
        print("  No stocks in universe. Run fetch_stock_universe.py first.")
        return

    print(f"Fetching Finnhub data for {len(symbols)} stocks...")

    # Sentiment
    print("  Fetching news sentiment...")
    sentiment_rows = fetch_news_sentiment(client, symbols)
    if sentiment_rows:
        upsert_many(
            "news_sentiment",
            ["symbol", "date", "bearish_pct", "bullish_pct",
             "buzz_score", "articles_count", "total_news_7d"],
            sentiment_rows
        )
        print(f"    Saved {len(sentiment_rows)} sentiment records")

    # Analyst recommendations
    print("  Fetching analyst recommendations...")
    rec_rows = fetch_analyst_recommendations(client, symbols)
    if rec_rows:
        upsert_many("fundamentals", ["symbol", "metric", "value"], rec_rows)
        print(f"    Saved {len(rec_rows)} recommendation data points")

    # Earnings calendar
    print("  Fetching earnings calendar...")
    earnings_rows = fetch_earnings_calendar(client)
    if earnings_rows:
        upsert_many(
            "earnings_calendar",
            ["symbol", "date", "eps_estimate", "revenue_estimate"],
            earnings_rows
        )
        print(f"    Saved {len(earnings_rows)} upcoming earnings events")

    print("Finnhub data fetch complete.")


if __name__ == "__main__":
    run()
