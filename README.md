# Druckenmiller Alpha System

A multi-factor equity convergence engine that synthesizes 35 independent intelligence modules into actionable stock signals, filtered through a 10-gate cascade to surface only the highest-conviction fat pitches. Built for systematic alpha generation with institutional-grade rigor.

## What It Does

The system runs a daily pipeline after US market close that:

1. **Fetches** fresh data — prices, fundamentals, macro indicators, news, filings, alternative data, prediction markets, regulatory events
2. **Scores** every asset in a 923-asset universe (S&P 500 + 400 + crypto + commodities) across 35 independent analytical lenses
3. **Gates** assets through a 10-gate cascade — from 923 → fat pitches — eliminating weak setups at each stage
4. **Converges** remaining scores into a single conviction signal per stock, weighted by the current macro regime
5. **Optimizes** module weights using Bayesian updating based on historical accuracy
6. **Alerts** you to high-conviction opportunities via email

The result is a dashboard showing which stocks have the most evidence stacked in their favor — and which are flashing warning signs.

## Architecture

```
WAT Framework (Workflows > Agents > Tools)

tools/              Python scripts — deterministic execution layer
  daily_pipeline.py      Orchestrates all 35+ pipeline phases
  convergence_engine.py  Synthesizes 35 module scores into final signal
  gate_engine.py         10-gate cascade (Universe → Fat Pitches)
  weight_optimizer.py    Bayesian weight updating from historical accuracy
  api.py                 FastAPI backend — core routes
  api_intelligence.py    Intelligence module routes (AI regulatory, execs, predictions)
  api_market_modules.py  Market module routes (worldview, energy, patterns, pairs)
  api_data_modules.py    Data module routes (earnings NLP, gov/labor/pharma intel)
  api_analytics.py       Analytics routes (performance, track record, weights)
  api_gates.py           10-gate cascade routes (10 endpoints)
                         Total: FastAPI backend across 6 route files
  db.py                  PostgreSQL schema (117 tables)
  config.py              Core thresholds and API keys
  config_modules.py      Convergence weights, regime profiles, per-module settings

dashboard/           Next.js frontend (tab-based, 4-group sidebar nav)
modal_app.py         Serverless deployment (3 cron jobs)
workflows/           Markdown SOPs
.tmp/                Intermediate data (disposable)
```

## The 35 Convergence Modules

| # | Module | Weight | What It Does |
|---|--------|--------|--------------|
| 1 | **On-Chain Intel** | 8% | Nansen whale flows + Etherscan on-chain activity |
| 2 | **Smart Money (13F)** | 7% | Tracks institutional position changes from SEC filings |
| 3 | **Worldview Model** | 7% | Macro thesis mapping to individual stocks (World Bank, IMF) |
| 4 | **Variant Perception** | 5% | Finds stocks where the market disagrees with fundamentals |
| 5 | **Analyst Intel** | 5% | Price target revisions, rating changes, consensus shifts |
| 6 | **Capital Flows** | 5% | Dark pool + fund flow proxies, sector rotation |
| 7 | **Short Interest** | 4% | FINRA short %, squeeze detection, crowded shorts |
| 8 | **Options Flow** | 4% | Unusual options activity and dealer positioning |
| 9 | **Foreign Intelligence** | 3% | Translated foreign-language market analysis (6 markets, 4 languages) |
| 10 | **Research Sources** | 3% | Aggregated analyst research signals (18 sources incl. GS, MS, JPM) |
| 11 | **News Displacement** | 3% | Material news not yet reflected in price |
| 12 | **Sector Experts** | 3% | Sector rotation and consolidation thesis |
| 13 | **Pairs Trading** | 3% | Cointegrated pairs, mean-reversion and runner detection |
| 14 | **M&A Intelligence** | 3% | Acquisition target profiling + rumor tracking (5-day half-life) |
| 15 | **Energy Intelligence** | 3% | Supply-demand balance, EIA data, energy sector signals |
| 16 | **Pattern & Options** | 3% | Chart patterns + unusual options activity |
| 17 | **Estimate Momentum** | 3% | EPS/revenue revision velocity, earnings surprise streaks |
| 18 | **Earnings NLP** | 3% | VADER + financial lexicon sentiment on 8-K filings from SEC EDGAR |
| 19 | **Retail Sentiment** | 3% | Stocktwits + social retail flow signals |
| 20 | **Main Signal** | 2% | Base convergence signal from technical + fundamental scoring |
| 21 | **Prediction Markets** | 2% | Polymarket data mapped to sector/stock impacts |
| 22 | **AI Regulatory** | 2% | Global AI regulation tracker across 9 jurisdictions |
| 23 | **Consensus Blindspots** | 2% | Howard Marks second-level thinking -- contrarian signals |
| 24 | **Government Intelligence** | 2% | WARN, OSHA, EPA, FCC, lobbying filings |
| 25 | **Labor Intelligence** | 2% | H-1B velocity, job postings, employee sentiment |
| 26 | **Supply Chain Intel** | 2% | Rail/shipping/trucking freight proxy for economic activity |
| 27 | **Digital Exhaust** | 2% | App store rankings, GitHub velocity, pricing and domain signals |
| 28 | **Pharma Intelligence** | 2% | Clinical trial phases, CMS utilization, Rx trends (healthcare only) |
| 29 | **Alternative Data** | 2% | Satellite (NDVI, ENSO), shipping, weather signals |
| 30 | **AAR Rail Intel** | 2% | American Association of Railroads carloading data + FRED rail proxies |
| 31 | **Ship Tracking** | 2% | BDI, port congestion, LNG shipping volumes |
| 32 | **Patent Intel** | 2% | USPTO filing velocity by technology class |
| 33 | **UCC Filings** | 2% | Secured debt distress signals from financing statements |
| 34 | **Board Interlocks** | 2% | DEF 14A governance patterns, interlocking board memberships |
| 35 | **Reddit / Social** | 1% | Social sentiment and momentum signals (info-only) |

**Regime-adaptive weights**: The system maintains 5 weight profiles (strong_risk_off through strong_risk_on) that shift module importance based on the current macro regime. A Bayesian weight optimizer updates weights daily based on historical prediction accuracy.

**Supporting modules** (not in convergence weights but feed the system):
- Insider Trading -- boosts/penalizes smart money scores based on SEC Form 4 filings
- Economic Dashboard -- 23 FRED indicators + Macro Heat Index
- Hyperliquid Gap Monitor -- weekend perp prices predict Monday equity gaps
- Accounting Forensics -- red flags that veto convergence signals
- Devil's Advocate -- contrarian risk analysis on high-conviction picks
- Base Rate Tracker -- historical accuracy tracking
- Paper Trader -- simulated portfolio performance
- Weight Optimizer -- Bayesian weight updating from track record
- Catalyst Engine -- event-driven catalyst scoring

## 10-Gate Cascade

Stocks must survive 10 sequential gates to be declared a fat pitch. Each gate eliminates based on a specific criterion:

| Gate | Name | Criterion | Typical Output |
|------|------|-----------|----------------|
| 0 | Universe | All tracked assets (equities + crypto + commodities) | 923 |
| 1 | Macro Regime | regime_score >= 30 | ~923 |
| 2 | Liquidity | ADV >= $15M, mktcap >= $500M (equities only) | ~900 |
| 3 | Forensic | No CRITICAL alerts, forensic_score >= 45 | ~860 |
| 4 | Sector Rotation | Leading/Improving quadrant, rotation_score >= 35 | ~340 |
| 5 | Technical Trend | technical_score >= 58 | ~150 |
| 6 | Fundamental | fundamental_score >= 42 (equities only) | ~65 |
| 7 | Smart Money | 13F >= 50 OR insider net > 0 OR capital_flow >= 65 | ~30 |
| 8 | Convergence | convergence_score >= 58 AND modules >= 5 | ~9 |
| 9 | Catalyst | catalyst_score >= 50 OR options_flow >= 70 | ~4 |
| 10 | Fat Pitch | composite >= 65, R:R >= 2.0 | ~4 |

The gate funnel is accessible via the **Investment Funnel** dashboard page (`/v2/gates`) as a 3-panel waterfall view.

## Dashboard

The dashboard uses a 4-group sidebar with 11 linked pages.

| Group | Page | Route | What You See |
|-------|------|-------|--------------|
| **Market** | Terminal | `/v2/terminal` | Full-screen stock terminal — signal details, insider activity, AI exec analysis |
| | Macro | `/macro` | Macro regime dashboard, FRED indicators, Heat Index |
| **Signals** | Conviction | `/` | System status, top signals, fat pitches, regime indicator |
| | Investment Funnel | `/v2/gates` | 10-gate waterfall -- 923 assets to fat pitches |
| | Screener | `/signals` | Insider, Blindspots, Displacement, Pairs, Est. Momentum, M&A, Alt Data |
| **Portfolio** | Holdings | `/v2/conviction` | Portfolio positions and conviction scores |
| | Alpha Stack | `/v2/alpha` | IC backtesting, module accuracy, alpha synthesis |
| | Performance | `/performance` | Module leaderboard, accuracy tracking, weight evolution |
| **Tools** | Risk | `/v2/risk` | Signal conflicts, stress test scenarios, thesis builder |
| | Journal | `/v2/journal` | Trade journal and notes |

Additional pages accessible via direct route:
- `/asset/[symbol]` — Asset detail (Overview, Convergence, Trade Setup, Regulatory tabs)
- `/intelligence` — AI regulatory tracker, exec signals, prediction markets
- `/energy` — EIA supply/demand, production, flows, global energy
- `/patterns` — Chart patterns, options flow, cycles, rotation
- `/discover` — AI-powered stock discovery and trading ideas
- `/reports` — Generated intelligence reports
- `/portfolio` — Paper trading performance

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, PostgreSQL (117 tables)
- **Frontend**: Next.js 14, TypeScript, Tailwind CSS, Lightweight Charts
- **Deployment**: Modal (serverless) — API endpoint + 3 scheduled cron jobs
- **Data Sources**: FMP, FRED, SEC EDGAR, Finnhub, Polymarket, Hyperliquid, NOAA, NASA MODIS, World Bank, IMF, Reddit, Serper, yfinance, Federal Register, EU AI Act sources, ClinicalTrials.gov, BLS H-1B, OSHA/EPA/FCC public APIs, App Store/GitHub, AAR rail data, BDI shipping, USPTO patents, UCC filings, Alpha Vantage, FINRA short interest, EPO patents, Nansen, Etherscan, USDA, Stocktwits, CoinGecko
- **LLM**: Google Gemini 2.5 Flash (news classification, M&A rumor scoring, foreign intel translation, regulatory event classification, prediction market mapping, earnings call sentiment)

## Setup

### Prerequisites

- **Python 3.11+**
- **Node.js 18+**
- **PostgreSQL** — local install or a cloud provider (Neon, Supabase, Railway all have free tiers)

### Step 1: Clone and install

```bash
git clone <this-repo>
cd druckenmiller-alpha

# Python backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Next.js dashboard
cd dashboard
npm install
cd ..
```

### Step 2: Get your API keys

Copy the template and fill in your keys:

```bash
cp .env.template .env
```

Open `.env` and add your credentials. Every key has a sign-up link in the template. The **minimum set** to get useful output:

| Key | Why | Free? |
|-----|-----|-------|
| `FMP_API_KEY` | Stock prices, fundamentals, universe | Free tier (250 req/day) |
| `FRED_API_KEY` | Macro indicators (GDP, unemployment, yield curve) | Free |
| `FINNHUB_API_KEY` | News, insider trades, SEC filings | Free tier |
| `GEMINI_API_KEY` | LLM-powered analysis (news classification, earnings NLP, M&A scoring) | Free tier |
| `DATABASE_URL` | PostgreSQL connection string | Free on Neon/Supabase |

Optional keys unlock more modules — the pipeline gracefully skips any module whose API key is missing.

### Step 3: Set up the database

The system uses PostgreSQL (117 tables). Tables are auto-created on first pipeline run — just provide a valid `DATABASE_URL` in `.env`:

```bash
# Example for local PostgreSQL:
DATABASE_URL=postgresql://localhost/druckenmiller

# Example for Neon (free tier):
DATABASE_URL=postgresql://user:pass@ep-xyz.us-east-2.aws.neon.tech/druckenmiller?sslmode=require
```

### Step 4: Run the pipeline

```bash
source venv/bin/activate
python -u -m tools.daily_pipeline
```

This runs all 70+ phases: fetch data, score every asset, run the 10-gate cascade, and generate signals. First run takes 10-20 minutes depending on API rate limits. Subsequent runs are faster (cached data).

The pipeline is designed to run after US market close (4 PM ET / 9 PM UTC). Each phase has error handling — if one module fails, the rest continue.

### Step 5: Start the API server

```bash
source venv/bin/activate
uvicorn tools.api:app --reload --port 8000
```

This starts the FastAPI backend (136+ endpoints across 6 route files). Browse the auto-generated docs at `http://localhost:8000/docs`.

### Step 6: Start the dashboard

```bash
cd dashboard
echo 'NEXT_PUBLIC_BACKEND_URL=http://localhost:8000' > .env.local
npm run dev -- --port 3333
```

Open `http://localhost:3333`. The dashboard connects to your local API server.

### Step 7 (optional): Deploy to Modal

For automated daily runs without keeping your machine on:

```bash
pip install modal
modal setup              # One-time login — creates a Modal account
modal deploy modal_app.py
```

This deploys:
- **API endpoint** — serves all routes as a serverless function
- **Daily pipeline** — runs Mon-Fri at 11 PM UTC (after US market close)
- **HL weekend monitor** — hourly on Sat/Sun (Hyperliquid gap tracking)
- **HL Monday backfill** — Monday 4 PM UTC (gap accuracy verification)

After deploying, update your dashboard to point at the Modal URL:

```bash
cd dashboard
echo 'NEXT_PUBLIC_BACKEND_URL=https://your-workspace--druckenmiller-api.modal.run' > .env.local
npm run build && npm run start   # or deploy to Vercel
```

### Step 8 (optional): Email alerts

To receive daily email alerts for high-conviction signals, add Gmail SMTP credentials to `.env`:

```
SMTP_USER=your_gmail@gmail.com
SMTP_PASS=your_app_password        # Generate at https://myaccount.google.com/apppasswords
EMAIL_TO=your_email@example.com
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `DATABASE_URL env var is not set` | Make sure `.env` exists and has a valid `DATABASE_URL` |
| Pipeline phase fails but continues | Normal — modules degrade gracefully. Check the summary at the end |
| `rate limit` errors from FMP/Finnhub | Free tiers have daily limits. Run the pipeline once per day after market close |
| Dashboard shows no data | Run the pipeline first, then start the API server |
| Missing module data | Add the corresponding API key to `.env` and re-run the pipeline |

## Pipeline Phases

The daily pipeline runs 70+ phases in sequence:

1. **Phase 1 (FETCH)** — Stock universe, prices, macro data, fundamentals, news, EIA energy data
2. **Phase 1.5 (ALT DATA)** — Alternative data (satellite NDVI, ENSO) + energy intelligence
3. **Phase 2 (SCORE)** — Market breadth, macro regime, technical scoring, fundamental scoring
4. **Phase 2.05 (ECON)** — 23 FRED economic indicators + Heat Index
5. **Phase 2.1 (TA GATE)** — Technical pre-screening gate
6. **Phase 2.15 (PATTERNS)** — Chart pattern detection + options intelligence
7. **Phase 2.5 (ALPHA EDGE)** — Accounting forensics, variant perception
8. **Phase 2.55 (ESTIMATE MOMENTUM)** — EPS/revenue revision velocity, surprise streaks
9. **Phase 2.6 (AI REGULATORY)** — Global AI regulation monitoring across 9 jurisdictions
10. **Phase 2.7 (EXTENDED)** — 13F filings, insider trading, research, Reddit, earnings NLP, foreign intel, news displacement, sector experts, pairs trading, M&A, energy intel, gov/labor/pharma intel, supply chain, digital exhaust
11. **Phase 2.75 (NEW INTEL)** — Short interest, retail sentiment, on-chain, analyst intel, options flow, capital flows, catalyst engine
12. **Phase 2.8 (CONSENSUS BLINDSPOTS)** — Second-level thinking, fat pitch detection
13. **Phase 3 (SIGNALS)** — Signal generation, position sizing, prediction markets
14. **Phase 3.5 (WORLDVIEW)** — Macro-to-stock thesis mapping (World Bank, IMF)
15. **Phase 3.55 (WEIGHT OPTIMIZER)** — Bayesian weight updating from track record
16. **Phase 3.9 (CONVERGENCE)** — Master synthesis across all 35 modules
17. **Phase 3.95 (DEVIL'S ADVOCATE)** — Contrarian risk analysis
18. **Phase 3.97 (BASE RATE)** — Historical accuracy tracking
19. **Phase 3.98 (PAPER TRADER)** — Simulated portfolio updates
20. **Phase 3.99 (GATES)** — 10-gate cascade, fat pitch selection
21. **Phase 4 (ALERTS)** — Check triggers, send email notifications

Each step has error handling — the pipeline continues on individual step failures and sends a failure summary email.

## Database

PostgreSQL. 117 tables including:
- `stock_universe` -- 923 assets tracked (S&P 500 + 400 + crypto + commodities)
- `convergence_signals` — final synthesized scores (all 35 module scores)
- Individual module tables (`smart_money_scores`, `pair_signals`, `ma_signals`, `estimate_momentum_signals`, `consensus_blindspot_signals`, `regulatory_signals`, `earnings_nlp_scores`, `gov_intel_scores`, `labor_intel_scores`, `supply_chain_scores`, `digital_exhaust_scores`, `pharma_intel_scores`, `short_interest_scores`, `retail_sentiment_scores`, `onchain_intel_scores`, `analyst_intel_scores`, `options_flow_intel_scores`, `capital_flows_scores`, `catalyst_scores`, etc.)
- `economic_dashboard` + `economic_heat_index` — macro indicators
- `hl_price_snapshots` + `hl_gap_signals` — Hyperliquid data
- `weight_history` + `weight_optimizer_log` — adaptive weight tracking
- `module_performance` + `signal_outcomes` — accuracy tracking
- `gate_results` — 10-gate cascade per-stock results

Tables are auto-created on first pipeline run. Works with any PostgreSQL provider (local, Neon, Supabase, Railway).

## Key Design Decisions

- **10-gate cascade** — Systematic funnel from 923 assets to ~4 fat pitches; each gate has a specific, measurable criterion
- **Regime-adaptive weights** — Module importance shifts with the macro environment, not static
- **Bayesian weight optimization** — Weights update daily based on historical prediction accuracy
- **Forensic veto** — Accounting red flags can block otherwise high-conviction signals
- **Second-level thinking** — Consensus Blindspots module detects crowded trades and contrarian opportunities
- **Bayesian priors** — M&A scoring uses sector base rates and market cap attractiveness curves
- **Rumor decay** — M&A rumors have a 5-day half-life with persistence bonuses for recurring mentions
- **Calibrated thresholds** — Scoring thresholds are set from actual data distribution, not arbitrary cutoffs
- **Graceful degradation** — Slow APIs (World Bank, ORNL) timeout gracefully and score 0 rather than crash the pipeline
- **Global regulatory coverage** — 9 jurisdictions, 15 regulatory bodies, jurisdiction-weighted scoring
