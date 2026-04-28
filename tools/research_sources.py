"""Curated Research Source Ingestion.

Scrapes and analyzes high-signal research publications:
  - Epoch AI (epochai.org) — AI compute trends, training run scaling
  - SemiAnalysis — semiconductor industry deep dives
  - Federal Reserve Research — monetary policy papers
  - BLS Reports — CPI/PPI/employment data

Pipeline per source:
  1. Serper API -> find 5 most recent relevant URLs
  2. Firecrawl -> extract clean markdown text from each URL
  3. Gemini Flash -> extract: mentioned tickers, sentiment, themes, summary
  4. Store to research_signals (skip cached URLs)

Outputs:
  - research_signals: per-article signals with ticker extraction
  - research_url_cache: prevents re-scraping the same URL

Usage: python -m tools.research_sources
"""

import sys
import json
import re
import time
from datetime import date
from pathlib import Path

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import requests

import anthropic

from tools.config import (
    SERPER_API_KEY, FIRECRAWL_API_KEY, ANTHROPIC_API_KEY, CLAUDE_MODEL,
    RESEARCH_SOURCES, RESEARCH_MIN_SCRAPE_CHARS, RESEARCH_SNIPPET_FALLBACK,
)
from tools.db import init_db, upsert_many, query, get_conn, serper_cache_get, serper_cache_set


# ── Constants ──────────────────────────────────────────────────────────

SERPER_URL = "https://google.serper.dev/search"
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"
MAX_ARTICLE_CHARS = 8_000
SCRAPE_DELAY = 2.0   # seconds between Firecrawl calls (rate limit)
MAX_URLS_PER_SOURCE = 5

# All known investment-relevant themes
ALL_THEMES = [
    "ai_capex", "compute_scaling", "training_runs", "inference_demand",
    "semiconductors", "supply_chain", "fab_capacity", "chip_shortage",
    "monetary_policy", "inflation", "cpi", "ppi", "labor_market", "employment",
    "rate_hike", "rate_cut", "quantitative_tightening", "liquidity",
    "energy", "oil", "natural_gas", "renewables", "power_demand",
    "cloud_computing", "data_centers", "hyperscalers",
    "geopolitics", "trade_war", "tariffs", "china",
    "regulation", "fiscal_policy", "trade_policy", "m_and_a",
    "central_banks", "credit_markets", "commodities_physical",
]


# ── Serper + Firecrawl (same pattern as founder_letter_analyzer.py) ────

def _serper_search(query_str: str, num_results: int = MAX_URLS_PER_SOURCE) -> list[dict]:
    """Search using Serper API, return list of {title, link, snippet, date}.
    Results are cached per-day in SQLite — same query never hits Serper twice in one day."""
    cached = serper_cache_get(query_str)
    if cached is not None:
        return cached
    if not SERPER_API_KEY:
        print("  Warning: SERPER_API_KEY not configured")
        return []
    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query_str, "num": num_results},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "date": item.get("date", ""),
            })
        serper_cache_set(query_str, results)
        return results
    except Exception as e:
        print(f"  Warning: Serper search failed: {e}")
        return []


def _firecrawl_scrape(url: str) -> str | None:
    """Scrape URL via Firecrawl API, return clean markdown text."""
    if not FIRECRAWL_API_KEY:
        print("  Warning: FIRECRAWL_API_KEY not configured")
        return None
    try:
        resp = requests.post(
            FIRECRAWL_URL,
            headers={
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"url": url, "formats": ["markdown"]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            content = data.get("data", {}).get("markdown", "") or ""
            # Strip boilerplate navigation/footer (heuristic: < 3 words per line)
            lines = [l for l in content.split("\n") if len(l.split()) >= 3 or l.startswith("#")]
            clean = "\n".join(lines)
            return clean[:MAX_ARTICLE_CHARS]
        return None
    except Exception as e:
        print(f"  Warning: Firecrawl scrape failed for {url}: {e}")
        return None


def _is_url_cached(url: str) -> bool:
    """Check if URL was already successfully scraped."""
    rows = query(
        "SELECT status FROM research_url_cache WHERE url = ?",
        [url],
    )
    return bool(rows and rows[0]["status"] == "ok")


def _cache_url(url: str, status: str):
    upsert_many(
        "research_url_cache",
        ["url", "scraped_at", "status"],
        [(url, date.today().isoformat(), status)],
    )


def _analyze_with_claude(
    text: str,
    title: str,
    source: str,
    relevance_tickers: list[str],
    is_snippet_only: bool = False,
) -> dict:
    """Use Claude Haiku to extract investment signals from article text."""
    if not ANTHROPIC_API_KEY:
        return {"tickers": [], "themes": [], "bullish_for": [], "bearish_for": [], "summary": title[:200], "relevance_score": 30}

    ticker_list = ", ".join(relevance_tickers) if relevance_tickers else "none specified"
    themes_list = ", ".join(ALL_THEMES)
    snippet_note = ""
    if is_snippet_only:
        snippet_note = "\nNOTE: Only title/snippet available (paywall). Lower relevance_score by 20. Set to 0 if insufficient signal."

    prompt = f"""You are an institutional investment analyst. Analyze this article and extract investment signals.{snippet_note}

Source: {source}
Title: {title}

Article text:
{text[:4000]}

Extract the following as JSON:
1. "tickers": array of objects with "symbol" (from this list: {ticker_list}) and "sentiment" (+1 bullish, -1 bearish, 0 neutral). Only tickers explicitly discussed.
2. "themes": array of applicable themes from: {themes_list}. Maximum 4.
3. "bullish_for": array of ticker symbols with positive investment implications
4. "bearish_for": array of ticker symbols with negative investment implications
5. "summary": 2-sentence investor summary with specific magnitude and mechanism.
6. "relevance_score": 0-100 (100=critical market-moving, 50=useful context, 0=not actionable)

Respond ONLY with valid JSON. No markdown, no explanation."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        match = re.search(r'\{.*\}', raw, flags=re.DOTALL)
        if match:
            raw = match.group(0)
        return json.loads(raw)
    except Exception as e:
        print(f"  Warning: Claude analysis failed: {e}")
        return {"tickers": [], "themes": [], "bullish_for": [], "bearish_for": [], "summary": title[:200], "relevance_score": 30}


def run():
    """Main entry: fetch and analyze research articles from curated sources."""
    init_db()
    today = date.today().isoformat()
    print("Research Sources: Ingesting curated intelligence...")

    total_articles = 0
    total_signals = 0

    for source_config in RESEARCH_SOURCES:
        source_name = source_config["name"]
        serper_query = source_config["serper_query"]
        relevance_tickers = source_config["relevance_tickers"]
        themes = source_config["themes"]

        print(f"\n  [{source_name.upper().replace('_', ' ')}]")
        print(f"  Query: {serper_query}")

        # Step 1: Discover URLs
        search_results = _serper_search(serper_query)
        if not search_results:
            print(f"  No results found")
            continue

        print(f"  Found {len(search_results)} URLs")

        for result in search_results:
            url = result["link"]
            title = result["title"]

            if _is_url_cached(url):
                print(f"  [cached] {title[:60]}...")
                continue

            print(f"  Scraping: {title[:60]}...")
            time.sleep(SCRAPE_DELAY)

            # Step 2: Scrape content (with paywall snippet fallback)
            text = _firecrawl_scrape(url)
            is_snippet_only = False

            if not text or len(text) < RESEARCH_MIN_SCRAPE_CHARS:
                if RESEARCH_SNIPPET_FALLBACK and result.get("snippet"):
                    text = f"TITLE: {title}\n\nSNIPPET: {result['snippet']}"
                    is_snippet_only = True
                    print(f"  [snippet fallback] {title[:60]}...")
                else:
                    _cache_url(url, "empty")
                    continue

            # Step 3: Analyze with Gemini
            analysis = _analyze_with_claude(text, title, source_name, relevance_tickers, is_snippet_only)

            # Step 4: Store
            tickers_data = analysis.get("tickers", [])
            bullish_for = json.dumps(analysis.get("bullish_for", []))
            bearish_for = json.dumps(analysis.get("bearish_for", []))
            key_themes = json.dumps(analysis.get("themes", themes))
            mentioned_tickers = json.dumps([t.get("symbol") for t in tickers_data if t.get("symbol")])
            article_summary = analysis.get("summary", title[:200])
            relevance_score = analysis.get("relevance_score", 30)

            # Store one row per article (with symbol=None for macro articles)
            # Plus additional rows per mentioned ticker for easier querying
            article_rows = []

            # Primary row (aggregate) — use '_MACRO_' sentinel so symbol NOT NULL is satisfied
            article_rows.append((
                '_MACRO_', today, source_name, url, title,
                0.0, relevance_score, key_themes,
                mentioned_tickers, bullish_for, bearish_for, article_summary,
            ))

            # Per-ticker rows for easy lookup
            for ticker_entry in tickers_data:
                sym = ticker_entry.get("symbol")
                sentiment = ticker_entry.get("sentiment", 0)
                if sym:
                    article_rows.append((
                        sym, today, source_name, f"{url}#{sym}", title,
                        float(sentiment), relevance_score, key_themes,
                        json.dumps([sym]), bullish_for, bearish_for, article_summary,
                    ))

            upsert_many(
                "research_signals",
                ["symbol", "date", "source", "url", "title", "sentiment",
                 "relevance_score", "key_themes", "mentioned_tickers",
                 "bullish_for", "bearish_for", "article_summary"],
                article_rows,
            )
            _cache_url(url, "ok")
            total_articles += 1
            total_signals += len(tickers_data)

            bullish = analysis.get("bullish_for", [])
            bearish = analysis.get("bearish_for", [])
            print(f"  Relevance: {relevance_score}/100 | "
                  f"Themes: {len(analysis.get('themes', []))} | "
                  f"Bullish: {bullish[:3]} | Bearish: {bearish[:3]}")

    # Summary: top recent research signals
    top_signals = query(
        """
        SELECT symbol, source, relevance_score, article_summary, date
        FROM research_signals
        WHERE symbol IS NOT NULL AND date >= date('now', '-7 days')
        ORDER BY relevance_score DESC
        LIMIT 10
        """
    )
    if top_signals:
        print(f"\n  TOP RESEARCH SIGNALS (last 7 days):")
        print(f"  {'Symbol':<8} {'Source':<16} {'Score':>6}  Summary")
        print(f"  {'-'*70}")
        for r in top_signals:
            summary_short = (r["article_summary"] or "")[:50]
            print(f"  {r['symbol']:<8} {r['source']:<16} {r['relevance_score']:>6.0f}  {summary_short}...")

    print(f"\nResearch complete: {total_articles} articles ingested, {total_signals} ticker signals extracted")


if __name__ == "__main__":
    run()
