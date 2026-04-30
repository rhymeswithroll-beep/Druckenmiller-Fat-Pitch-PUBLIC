"""Database helpers for the Druckenmiller Alpha System.
PostgreSQL connection management, query helpers, and bulk upsert.
Connection: DATABASE_URL env var (postgresql://user:pass@host:5432/db).
"""
import os
import re as _re
import threading
from contextlib import contextmanager

_init_db_lock = threading.Lock()
_init_db_done = False

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# LOCAL SQLite — tables that are pipeline-internal (large, high-churn) live
# here instead of Neon to avoid burning the cloud transfer quota.
# The API server is co-located on the same machine, so it can read these too.
# ---------------------------------------------------------------------------
_SQLITE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".tmp", "druckenmiller.db"
)

LOCAL_TABLES = frozenset({
    # ── Always-local (pipeline-internal, large, or high-churn) ──
    "price_data",           # 903 stocks × 500+ days — the single biggest Neon consumer
    "stock_universe",       # 903 rows read by every module on every run
    "macro_indicators",     # FRED time-series, read heavily by macro/technical modules
    "earnings_calendar",    # Pipeline-internal lookup only
    "insider_transactions", # 14K+ rows, upserted fresh every run — Neon killer
    "insider_signals",      # Module-internal output, read back by same module
    "edgar_insider_raw",    # EDGAR Form 4 XML parsed transactions — feeds insider_transactions
    "edgar_filing_metadata",# EDGAR Form 4 filing metadata — daily refresh, local only
    "foreign_ticker_map",   # Static ~80-row ADR map; Neon pooler intermittently loses DDL
    "ma_signals",           # M&A target scoring — written and read locally
    "ma_rumors",            # M&A rumor headlines — written and read locally
    # ── Migrated from Neon — high dashboard traffic, no multi-machine need ──
    "signals",              # Main scoring output: 903 rows/day, queried on every dashboard load
    "convergence_signals",  # Convergence scores: 903 rows/day, heatmap + sector drill-down
    "technical_scores",     # Technical module output: asset detail pages
    "fundamental_scores",   # Fundamental module output: asset detail pages
    "sector_rotation",      # Sector RRG data: terminal sector panel
    "market_breadth",       # Breadth metrics: terminal header
    "macro_scores",         # Macro scoring: terminal + macro page
    "signal_outcomes",      # Forward-return tracking: Performance page (valuable history)
    "gate_results",         # Funnel gate results: gate cascade tracking
    "gate_run_history",     # Per-run gate summary stats
    "fundamentals",         # Raw yfinance metrics (marketCap, PE, etc.) — read by Gate 2
    # ── Additional modules that were silently writing to Neon (quota-exceeded) ──
    "economic_dashboard",           # FRED processed indicators: economic tab
    "economic_heat_index",          # FRED heat index: economic tab
    "consensus_blindspot_signals",  # Howard Marks blindspot signals
    "prediction_market_signals",    # Polymarket signals: per-symbol
    "prediction_market_raw",        # Polymarket raw markets
    "estimate_momentum_signals",    # Estimate revision momentum
    "estimate_snapshots",           # EPS/revenue snapshots
    "regulatory_signals",           # AI regulatory signals: per-symbol
    "regulatory_events",            # Regulatory events raw
    "ai_exec_signals",              # AI exec investment tracker: per-symbol
    "ai_exec_investments",          # AI exec investments raw
    "ai_exec_url_cache",            # AI exec URL cache
    "alternative_data",             # Alt data signals (weather, crop, etc.) — pipeline-local
    "alt_data_scores",              # Aggregated alt data scores per symbol — pipeline-local
})


def _get_sqlite():
    """Open a fresh SQLite connection to the local DB (cheap for a local file)."""
    import sqlite3
    os.makedirs(os.path.dirname(_SQLITE_PATH), exist_ok=True)
    conn = sqlite3.connect(_SQLITE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def get_sqlite_conn():
    """Public alias for _get_sqlite() — for pipeline modules that write LOCAL_TABLES directly."""
    return _get_sqlite()


def _init_local_db():
    """Create local SQLite tables if they don't exist."""
    conn = _get_sqlite()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_universe (
                symbol TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
                market_cap REAL, asset_class TEXT DEFAULT 'stock'
            );
            CREATE TABLE IF NOT EXISTS price_data (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
                volume REAL, adj_close REAL, asset_class TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS macro_indicators (
                indicator_id TEXT, date TEXT, value REAL,
                PRIMARY KEY (indicator_id, date)
            );
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                symbol TEXT, date TEXT, earnings_date TEXT, estimate_eps REAL,
                actual_eps REAL, surprise_pct REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS insider_transactions (
                symbol TEXT, date TEXT, insider_name TEXT, insider_title TEXT,
                transaction_type TEXT, shares REAL, price REAL, value REAL,
                shares_owned_after REAL, filing_url TEXT, source TEXT,
                PRIMARY KEY (symbol, date, insider_name, transaction_type)
            );
            CREATE TABLE IF NOT EXISTS insider_signals (
                symbol TEXT, date TEXT, insider_score REAL, cluster_buy INTEGER,
                cluster_count INTEGER, large_buys_count INTEGER,
                total_buy_value_30d REAL, total_sell_value_30d REAL,
                unusual_volume_flag INTEGER, top_buyer TEXT, narrative TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS foreign_ticker_map (
                local_ticker TEXT PRIMARY KEY, adr_ticker TEXT,
                company_name_local TEXT, company_name_english TEXT,
                market TEXT, sector TEXT, in_universe INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS signals (
                symbol TEXT, date TEXT, composite_score REAL, signal TEXT, sector TEXT,
                technical_score REAL, fundamental_score REAL, asset_class TEXT,
                macro_score REAL, entry_price REAL, stop_loss REAL, target_price REAL,
                rr_ratio REAL, position_size_shares REAL, position_size_dollars REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS convergence_signals (
                symbol TEXT NOT NULL, date TEXT NOT NULL, convergence_score REAL NOT NULL,
                module_count INTEGER, conviction_level TEXT, forensic_blocked INTEGER DEFAULT 0,
                main_signal_score REAL, smartmoney_score REAL, worldview_score REAL,
                variant_score REAL, research_score REAL, reddit_score REAL,
                active_modules TEXT, narrative TEXT,
                news_displacement_score REAL, alt_data_score REAL, sector_expert_score REAL,
                foreign_intel_score REAL, pairs_score REAL, ma_score REAL,
                energy_intel_score REAL, prediction_markets_score REAL,
                pattern_options_score REAL, estimate_momentum_score REAL,
                ai_regulatory_score REAL, consensus_blindspots_score REAL,
                earnings_nlp_score REAL, gov_intel_score REAL, labor_intel_score REAL,
                supply_chain_score REAL, digital_exhaust_score REAL, pharma_intel_score REAL,
                aar_rail_score REAL, ship_tracking_score REAL, patent_intel_score REAL,
                ucc_filings_score REAL, board_interlocks_score REAL,
                short_interest_score REAL, retail_sentiment_score REAL,
                onchain_intel_score REAL, analyst_intel_score REAL,
                options_flow_score REAL, capital_flows_score REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS technical_scores (
                symbol TEXT, date TEXT, trend_score REAL, momentum_score REAL,
                volatility_score REAL, volume_score REAL, total_score REAL,
                breakout_score REAL, relative_strength_score REAL, breadth_score REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS fundamental_scores (
                symbol TEXT, date TEXT, value_score REAL, quality_score REAL,
                growth_score REAL, total_score REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS sector_rotation (
                sector TEXT, date TEXT, rs_ratio REAL, rs_momentum REAL,
                quadrant TEXT, rotation_score REAL, score REAL,
                PRIMARY KEY (sector, date)
            );
            CREATE TABLE IF NOT EXISTS market_breadth (
                date TEXT PRIMARY KEY, advancers INTEGER, decliners INTEGER,
                new_highs INTEGER, new_lows INTEGER, adv_dec_ratio REAL,
                advance_decline_ratio REAL, pct_above_200dma REAL,
                breadth_score REAL, sector_rotation TEXT
            );
            CREATE TABLE IF NOT EXISTS macro_scores (
                date TEXT PRIMARY KEY, regime TEXT, regime_score REAL, total_score REAL,
                fed_funds_score REAL, m2_score REAL, real_rates_score REAL,
                yield_curve_score REAL, credit_spreads_score REAL,
                dxy_score REAL, vix_score REAL, details TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                symbol TEXT NOT NULL, signal_date TEXT NOT NULL,
                conviction_level TEXT, convergence_score REAL, module_count INTEGER,
                active_modules TEXT, regime_at_signal TEXT, sector TEXT,
                market_cap_bucket TEXT, entry_price REAL,
                price_1d REAL, return_1d REAL, price_5d REAL, return_5d REAL,
                price_10d REAL, return_10d REAL, price_20d REAL, return_20d REAL,
                price_30d REAL, return_30d REAL, price_60d REAL, return_60d REAL,
                price_90d REAL, return_90d REAL,
                hit_target INTEGER, hit_stop INTEGER,
                da_risk_score REAL, da_warning INTEGER DEFAULT 0,
                PRIMARY KEY (symbol, signal_date)
            );
            CREATE TABLE IF NOT EXISTS gate_results (
                symbol TEXT, date TEXT,
                gate_0 INTEGER DEFAULT 1, gate_1 INTEGER, gate_2 INTEGER,
                gate_3 INTEGER, gate_4 INTEGER, gate_5 INTEGER, gate_6 INTEGER,
                gate_7 INTEGER, gate_8 INTEGER, gate_9 INTEGER, gate_10 INTEGER,
                last_gate_passed INTEGER, fail_reason TEXT, asset_class TEXT,
                entry_mode TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS gate_run_history (
                run_id TEXT PRIMARY KEY, date TEXT, total_assets INTEGER,
                gate_1_passed INTEGER, gate_2_passed INTEGER, gate_3_passed INTEGER,
                gate_4_passed INTEGER, gate_5_passed INTEGER, gate_6_passed INTEGER,
                gate_7_passed INTEGER, gate_8_passed INTEGER, gate_9_passed INTEGER,
                gate_10_passed INTEGER, run_time_seconds REAL
            );
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
                updated_at TEXT,
                PRIMARY KEY (symbol, metric)
            );
            CREATE TABLE IF NOT EXISTS economic_dashboard (
                indicator_id TEXT, date TEXT, category TEXT, name TEXT, value REAL,
                prev_value REAL, mom_change REAL, yoy_change REAL, zscore REAL,
                trend TEXT, signal TEXT, last_updated TEXT,
                PRIMARY KEY (indicator_id, date)
            );
            CREATE TABLE IF NOT EXISTS economic_heat_index (
                date TEXT PRIMARY KEY, heat_index REAL, improving_count INTEGER,
                deteriorating_count INTEGER, stable_count INTEGER,
                leading_count INTEGER, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS consensus_blindspot_signals (
                symbol TEXT, date TEXT, cbs_score REAL, gap_type TEXT,
                cycle_position TEXT, details TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS prediction_market_signals (
                symbol TEXT, date TEXT, pm_score REAL, market_count INTEGER,
                net_impact REAL, status TEXT, narrative TEXT, sector TEXT, details TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS prediction_market_raw (
                market_id TEXT, date TEXT, question TEXT, impact_category TEXT,
                yes_probability REAL, volume REAL, liquidity REAL, direction TEXT,
                confidence REAL, specific_symbols TEXT, rationale TEXT, end_date TEXT,
                probability REAL, category TEXT, relevance TEXT,
                PRIMARY KEY (market_id, date)
            );
            CREATE TABLE IF NOT EXISTS estimate_snapshots (
                symbol TEXT, date TEXT, eps_current REAL, eps_next REAL,
                rev_current REAL, rev_next REAL, details TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS estimate_momentum_signals (
                symbol TEXT, date TEXT, em_score REAL,
                eps_velocity_7d REAL, eps_velocity_30d REAL, eps_velocity_90d REAL,
                velocity_score REAL, rev_velocity_score REAL,
                acceleration REAL, acceleration_score REAL,
                beat_streak INTEGER, miss_streak INTEGER,
                avg_surprise_pct REAL, surprise_score REAL,
                dispersion_pct REAL, dispersion_score REAL,
                sector_rank_pct REAL, sector_rank_score REAL,
                hist_eps_velocity REAL, hist_rev_velocity REAL, hist_score REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS regulatory_signals (
                symbol TEXT, date TEXT, reg_score REAL, event_count INTEGER,
                net_impact REAL, status TEXT, narrative TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS regulatory_events (
                event_id TEXT, date TEXT, title TEXT, source TEXT, severity REAL,
                category TEXT, direction TEXT, jurisdiction TEXT,
                affected_symbols TEXT, details TEXT,
                PRIMARY KEY (event_id, date)
            );
            CREATE TABLE IF NOT EXISTS ai_exec_signals (
                symbol TEXT, date TEXT, score REAL, ai_exec_score REAL,
                exec_count INTEGER, top_exec TEXT, top_activity TEXT,
                sector_signal TEXT, narrative TEXT,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS ai_exec_investments (
                exec_name TEXT, exec_org TEXT, exec_prominence TEXT, activity_type TEXT,
                target_company TEXT, target_ticker TEXT, target_sector TEXT,
                investment_amount REAL, funding_round TEXT, is_public INTEGER,
                ipo_expected INTEGER, ipo_timeline TEXT, date_reported TEXT,
                confidence REAL, summary TEXT, source_url TEXT, source TEXT,
                raw_score REAL, scan_date TEXT,
                PRIMARY KEY (exec_name, target_company, date_reported)
            );
            CREATE TABLE IF NOT EXISTS ai_exec_url_cache (
                url TEXT PRIMARY KEY, fetched_date TEXT, content TEXT,
                scraped_at TEXT, status TEXT
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _extract_table(sql: str):
    """Extract the primary table name from SQL for local/remote routing."""
    m = _re.search(
        r'(?:FROM|INTO|UPDATE|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+(\w+)',
        sql, flags=_re.IGNORECASE,
    )
    return m.group(1).lower() if m else None


def _sqlite_query(sql, params=None):
    """Run a SELECT against the local SQLite DB, return list of dicts."""
    conn = _get_sqlite()
    try:
        cur = conn.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _sqlite_upsert(table: str, columns: list, rows: list):
    """Bulk INSERT OR REPLACE into a local SQLite table."""
    if not rows:
        return
    col_str = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"
    conn = _get_sqlite()
    try:
        conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()


def serper_cache_get(query: str) -> list | None:
    """Return cached Serper results for query if run today, else None."""
    import json as _json
    conn = _get_sqlite()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS serper_search_cache (
            query TEXT, date TEXT, results TEXT, PRIMARY KEY (query, date))""")
        conn.commit()
        today = __import__('datetime').date.today().isoformat()
        row = conn.execute(
            "SELECT results FROM serper_search_cache WHERE query=? AND date=?",
            (query, today)
        ).fetchone()
        return _json.loads(row[0]) if row else None
    finally:
        conn.close()


def serper_cache_set(query: str, results: list):
    """Cache Serper results for query for today."""
    import json as _json
    conn = _get_sqlite()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS serper_search_cache (
            query TEXT, date TEXT, results TEXT, PRIMARY KEY (query, date))""")
        today = __import__('datetime').date.today().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO serper_search_cache (query, date, results) VALUES (?,?,?)",
            (query, today, _json.dumps(results))
        )
        conn.commit()
    finally:
        conn.close()


def _sqlite_execute(sql, params=None):
    """Run a single DML statement against the local SQLite DB."""
    conn = _get_sqlite()
    try:
        conn.execute(sql, params or [])
        conn.commit()
    finally:
        conn.close()


_DATABASE_URL = os.environ.get("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is not set. Set it to the Neon connection string in .env")

_pool: pg_pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

# TCP keepalive options — prevent Neon/PgBouncer from silently closing idle connections
# during long pipeline phases (10+ min) that don't touch the DB while fetching data.
_CONNECT_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 10,
    "keepalives_count": 5,
}


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = pg_pool.ThreadedConnectionPool(2, 30, _DATABASE_URL, **_CONNECT_KWARGS)
    return _pool


def _reset_pool():
    """Tear down and recreate the connection pool after a fatal SSL error."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None


class _PgCursorWrapper:
    """Cursor wrapper that auto-converts ? placeholders and SQLite date funcs."""

    def __init__(self, cursor):
        self._cur = cursor

    def execute(self, sql, params=None):
        # execute_batch passes pre-mogrified bytes — skip _to_pg conversion
        # and do NOT pass params (already rendered; passing [] triggers format substitution)
        if isinstance(sql, bytes):
            self._cur.execute(sql)
        else:
            self._cur.execute(_to_pg(sql), params or [])
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(_to_pg(sql), seq)
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    def __iter__(self):
        return iter(self._cur)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._cur.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _PgConnWrapper:
    """Thin wrapper adding SQLite-compat shims (executescript, execute) to psycopg2 conn."""

    def __init__(self, conn):
        self._conn = conn

    # --- SQLite compat ---
    def executescript(self, sql: str):
        """Run multiple semicolon-separated DDL statements (SQLite compat)."""
        with self._conn.cursor() as cur:
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(_to_pg(stmt))
        self._conn.commit()

    def execute(self, sql: str, params=None):
        """Single-statement execute returning a cursor-like object (SQLite compat)."""
        sql = _to_pg(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or [])
        return cur

    def executemany(self, sql: str, seq_of_params):
        """SQLite compat: run one statement for each param tuple."""
        sql = _to_pg(sql)
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, seq_of_params)
        self._conn.commit()

    # --- psycopg2 passthrough ---
    def cursor(self, *args, **kwargs):
        raw = self._conn.cursor(*args, **kwargs)
        return _PgCursorWrapper(raw)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        """Return connection to pool instead of closing it."""
        _release(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        _release(self)
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_conn():
    """Return a psycopg2 connection (wrapped for SQLite API compat) from the thread pool.
    Validates the connection is live with a ping before returning it; retries once by
    resetting the whole pool if the connection is dead (covers silent SSL drops)."""
    for attempt in range(2):
        raw = None
        try:
            raw = _get_pool().getconn()
            # Ping to catch silently-dead connections (closed SSL but raw.closed still 0)
            with raw.cursor() as _cur:
                _cur.execute("SELECT 1")
            return _PgConnWrapper(raw)
        except psycopg2.OperationalError:
            if raw is not None:
                try:
                    _get_pool().putconn(raw, close=True)
                except Exception:
                    pass
            if attempt == 0:
                _reset_pool()
            else:
                raise
    raise psycopg2.OperationalError("Could not obtain a working database connection")


def _release(conn):
    try:
        raw = conn._conn if isinstance(conn, _PgConnWrapper) else conn
        # Return broken connections as closed so the pool discards them
        if raw.closed or raw.status not in (
            psycopg2.extensions.STATUS_READY,
            psycopg2.extensions.STATUS_BEGIN,
        ):
            _get_pool().putconn(raw, close=True)
        else:
            _get_pool().putconn(raw)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Column cache — avoids hitting information_schema on every upsert_many call.
# ---------------------------------------------------------------------------
_col_cache: dict[str, set[str]] = {}


def _pg_columns(table: str) -> set[str]:
    """Return the set of column names that exist in the Postgres table."""
    if table not in _col_cache:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s""",
                    [table],
                )
                _col_cache[table] = {r[0] for r in cur.fetchall()}
        finally:
            _release(conn)
    return _col_cache[table]


def _invalidate_col_cache(table: str):
    """Call after ALTER TABLE to refresh the cache for that table."""
    _col_cache.pop(table, None)


# ---------------------------------------------------------------------------
# PRIMARY KEY MAP — used by upsert_many for ON CONFLICT resolution.
# Tables with SERIAL id (portfolio, intelligence_reports, etc.) are inserted
# directly via SQL by the pipeline, not via upsert_many, so they're omitted.
# ---------------------------------------------------------------------------
TABLE_PKS: dict[str, list[str]] = {
    "stock_universe":              ["symbol"],
    "price_data":                  ["symbol", "date"],
    "technical_scores":            ["symbol", "date"],
    "fundamental_scores":          ["symbol", "date"],
    "fundamentals":                ["symbol", "metric"],
    "signals":                     ["symbol", "date"],
    "macro_indicators":            ["indicator_id", "date"],
    "macro_scores":                ["date"],
    "market_breadth":              ["date"],
    "sector_rotation":             ["sector", "date"],
    "news_sentiment":              ["symbol", "date", "headline"],
    "watchlist":                   ["symbol"],
    "smart_money_scores":          ["symbol", "date"],
    "filings_13f":                 ["cik", "symbol", "period_of_report"],
    "worldview_signals":           ["symbol", "date"],
    "foreign_intel_signals":       ["symbol", "date", "source"],
    "foreign_intel_url_cache":     ["url"],
    "foreign_ticker_map":          ["local_ticker"],
    "research_signals":            ["symbol", "date", "source"],
    "research_url_cache":          ["url"],
    "news_displacement":           ["symbol", "date"],
    "reddit_signals":              ["symbol", "date", "subreddit"],
    "alt_data_scores":             ["symbol", "date", "source"],
    "alternative_data":            ["date", "source", "indicator"],
    "convergence_signals":         ["symbol", "date"],
    "signal_outcomes":             ["symbol", "signal_date"],
    "module_performance":          ["report_date", "module_name", "regime", "sector"],
    "weight_history":              ["date", "regime", "module_name"],
    "weight_optimizer_log":        ["date", "action"],
    "sector_expert_signals":       ["symbol", "date"],
    "pattern_scan":                ["symbol", "date"],
    "pattern_options_signals":     ["symbol", "date"],
    "options_intel":               ["symbol", "date"],
    "variant_analysis":            ["symbol", "date"],
    "devils_advocate":             ["symbol", "date"],
    "transcript_analysis":         ["symbol", "date"],
    "letter_analysis":             ["symbol", "date"],
    "forensic_alerts":             ["symbol", "date", "alert_type"],
    "earnings_calendar":           ["symbol", "date"],
    "pair_relationships":          ["symbol_a", "symbol_b"],
    "pair_spreads":                ["symbol_a", "symbol_b", "date"],
    "pair_signals":                ["symbol_a", "symbol_b", "date", "signal_type"],
    "ma_signals":                  ["symbol", "date"],
    "ma_rumors":                   ["symbol", "date", "source"],
    "insider_transactions":        ["symbol", "date", "insider_name", "transaction_type"],
    "insider_signals":             ["symbol", "date"],
    "economic_dashboard":          ["indicator_id", "date"],
    "economic_heat_index":         ["date"],
    "hl_price_snapshots":          ["ticker", "timestamp", "deployer"],
    "hl_gap_signals":              ["ticker", "date"],
    "hl_deployer_spreads":         ["ticker", "date", "deployer_a", "deployer_b"],
    "prediction_market_signals":   ["symbol", "date"],
    "prediction_market_raw":       ["market_id", "date"],
    "world_macro_indicators":      ["indicator", "country", "date"],
    "estimate_snapshots":          ["symbol", "date"],
    "estimate_momentum_signals":   ["symbol", "date"],
    "regulatory_signals":          ["symbol", "date"],
    "regulatory_events":           ["event_id", "date"],
    "consensus_blindspot_signals": ["symbol", "date"],
    "ai_exec_signals":             ["symbol", "date"],
    "ai_exec_investments":         ["exec_name", "target_company", "date_reported"],
    "ai_exec_url_cache":           ["url"],
    "energy_intel_signals":        ["symbol", "date"],
    "energy_eia_enhanced":         ["series_id", "date"],
    "energy_supply_anomalies":     ["date", "series_id", "anomaly_type"],
    "energy_trade_flows":          ["reporter", "partner", "commodity_code", "period", "trade_flow"],
    "energy_seasonal_norms":       ["series_id", "week_of_year"],
    "energy_jodi_data":            ["country", "indicator", "date"],
    "global_energy_benchmarks":    ["benchmark_id", "date"],
    "global_energy_curves":        ["curve_id", "date", "months_out"],
    "global_energy_spreads":       ["spread_id", "date"],
    "global_energy_carbon":        ["market_id", "date"],
    "global_energy_signals":       ["symbol", "date"],
    "signal_conflicts":            ["symbol", "date", "conflict_type"],
    "thesis_snapshots":            ["date", "thesis"],
    "thesis_alerts":               ["date", "thesis", "alert_type"],
    "earnings_transcripts":        ["symbol", "quarter"],
    "earnings_nlp_scores":         ["symbol", "date"],
    "gov_intel_raw":               ["symbol", "date", "source", "event_type"],
    "gov_intel_scores":            ["symbol", "date"],
    "labor_intel_raw":             ["symbol", "date", "source", "metric"],
    "labor_intel_scores":          ["symbol", "date"],
    "supply_chain_raw":            ["date", "source", "metric"],
    "supply_chain_scores":         ["symbol", "date"],
    "digital_exhaust_raw":         ["symbol", "date", "source", "metric"],
    "digital_exhaust_scores":      ["symbol", "date"],
    "pharma_intel_raw":            ["symbol", "date", "source", "metric"],
    "pharma_intel_scores":         ["symbol", "date"],
    "stress_test_results":         ["date", "scenario"],
    "concentration_risk":          ["date"],
    "cross_asset_opportunities":   ["symbol", "date"],
    "signal_ic_results":           ["module", "signal_date", "horizon_days", "regime"],
    "module_ic_summary":           ["module", "regime", "horizon_days"],
    "narrative_signals":           ["narrative_id", "date"],
    "narrative_asset_map":         ["narrative_id", "symbol", "date"],
    "stress_backtest_results":     ["crisis", "sector_etf"],
    "stress_calibration":          ["scenario", "sector"],
    "funnel_overrides":            ["symbol", "stage"],
    "asset_class_signals":         ["asset_class", "date"],
    "funnel_snapshot":             ["date", "run_id"],
    "gate_results":                ["symbol", "date"],
    "gate_overrides":              ["symbol", "gate"],
    "gate_run_history":            ["run_id"],
    "fmp_short_interest":          ["symbol", "date"],
    "fmp_analyst_data":            ["symbol", "date"],
    "fmp_dcf":                     ["symbol", "date"],
    "fmp_institutional":           ["symbol", "date"],
    "stocktwits_sentiment":        ["symbol", "date"],
    "coingecko_data":              ["asset", "date"],
    "edgar_insider_raw":           ["accession", "owner_name"],
    "edgar_filing_metadata":       ["accession"],
    "av_technical_indicators":     ["symbol", "date"],
    "finra_short_interest":        ["symbol", "date"],
    "nansen_signals":              ["asset", "date"],
    "etherscan_signals":           ["date"],
    "usda_commodity_data":         ["commodity", "date"],
    "epo_patents":                 ["symbol", "date"],
    "patent_intel_raw":            ["symbol", "date"],
    "patent_intel_scores":         ["symbol", "date"],
    "ucc_filings_scores":          ["symbol", "date"],
    "options_flow_scores":         ["symbol", "date"],
    "aar_rail_raw":                ["date", "commodity_type"],
    "aar_rail_scores":             ["symbol", "date"],
    "ship_tracking_raw":           ["date", "source", "metric"],
    "ship_tracking_scores":        ["symbol", "date"],
    "board_interlocks_scores":     ["symbol", "date"],
    "energy_stress_scores":        ["date", "symbol", "scenario"],
    "energy_regime":               ["date"],
    "energy_regulatory_signals":   ["date", "source", "headline"],
    "analyst_scores":              ["symbol", "date"],
    "capital_flow_scores":         ["symbol", "date"],
    "catalyst_scores":             ["symbol", "date"],
    "onchain_scores":              ["asset", "date"],
    "short_interest_scores":       ["symbol", "date"],
    "retail_sentiment_scores":     ["symbol", "date"],
    "intelligence_reports":        ["topic", "topic_type", "expert_type", "regime"],
}


def init_db():
    """Ensure all core tables exist (CREATE IF NOT EXISTS). Thread-safe: runs once per process,
    and uses a PG advisory lock to prevent concurrent DDL races across Modal workers.
    Also initialises the local SQLite DB for LOCAL_TABLES."""
    global _init_db_done
    with _init_db_lock:
        if _init_db_done:
            return
        _init_db_done = True
    # Always init local SQLite first — no network dependency
    _init_local_db()
    conn = get_conn()
    cur = conn.cursor()
    try:
        # Advisory lock: serialises init_db across all processes/workers on this DB.
        # pg_advisory_xact_lock is auto-released on commit/rollback — no manual unlock needed.
        cur.execute("SELECT pg_advisory_xact_lock(42424242)")

        # Drop tables whose schemas were wrong in early builds so CREATE recreates them correctly.
        for tbl in ["foreign_ticker_map", "foreign_intel_signals", "alternative_data",
                    "ai_exec_investments"]:
            cur.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()

        statements = [
            # stock_universe, price_data, macro_indicators, earnings_calendar → LOCAL_TABLES (SQLite)
            """CREATE TABLE IF NOT EXISTS technical_scores (symbol TEXT, date TEXT, trend_score REAL, momentum_score REAL, volatility_score REAL, volume_score REAL, total_score REAL, breakout_score REAL, relative_strength_score REAL, breadth_score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS fundamental_scores (symbol TEXT, date TEXT, value_score REAL, quality_score REAL, growth_score REAL, total_score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS fundamentals (symbol TEXT NOT NULL, metric TEXT NOT NULL, value REAL, updated_at TEXT DEFAULT NOW(), PRIMARY KEY (symbol, metric))""",
            """CREATE TABLE IF NOT EXISTS signals (symbol TEXT, date TEXT, composite_score REAL, signal TEXT, sector TEXT, technical_score REAL, fundamental_score REAL, asset_class TEXT, macro_score REAL, entry_price REAL, stop_loss REAL, target_price REAL, rr_ratio REAL, position_size_shares REAL, position_size_dollars REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS macro_scores (date TEXT PRIMARY KEY, regime TEXT, regime_score REAL, total_score REAL, fed_funds_score REAL, m2_score REAL, real_rates_score REAL, yield_curve_score REAL, credit_spreads_score REAL, dxy_score REAL, vix_score REAL, details TEXT)""",
            """CREATE TABLE IF NOT EXISTS market_breadth (date TEXT PRIMARY KEY, advancers INTEGER, decliners INTEGER, new_highs INTEGER, new_lows INTEGER, adv_dec_ratio REAL, advance_decline_ratio REAL, pct_above_200dma REAL, breadth_score REAL, sector_rotation TEXT)""",
            """CREATE TABLE IF NOT EXISTS sector_rotation (sector TEXT, date TEXT, rs_ratio REAL, rs_momentum REAL, quadrant TEXT, rotation_score REAL, score REAL, PRIMARY KEY (sector, date))""",
            """CREATE TABLE IF NOT EXISTS news_sentiment (symbol TEXT, date TEXT, headline TEXT, source TEXT, sentiment REAL, relevance REAL, PRIMARY KEY (symbol, date, headline))""",
            """CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY, notes TEXT, alert_price_above REAL, alert_price_below REAL, alert_tech_above REAL)""",
            """CREATE TABLE IF NOT EXISTS portfolio (id SERIAL PRIMARY KEY, symbol TEXT, shares REAL, entry_price REAL, entry_date TEXT, stop_loss REAL, target REAL, target_price REAL, notes TEXT, asset_class TEXT DEFAULT 'equity', status TEXT DEFAULT 'open', exit_price REAL, exit_date TEXT, entry_thesis TEXT, entry_convergence_snapshot TEXT)""",
            """CREATE TABLE IF NOT EXISTS smart_money_scores (symbol TEXT, date TEXT, manager_count INTEGER, conviction_score REAL, top_holders TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS filings_13f (cik TEXT, manager_name TEXT, symbol TEXT, period_of_report TEXT, filing_date TEXT, accession_number TEXT, cusip TEXT, shares_held REAL, market_value REAL, investment_type TEXT, prior_shares REAL, change_shares REAL, change_pct REAL, action TEXT, rank_in_portfolio INTEGER, portfolio_pct REAL, PRIMARY KEY (cik, symbol, period_of_report))""",
            """CREATE TABLE IF NOT EXISTS worldview_signals (symbol TEXT, date TEXT, regime TEXT, thesis_alignment_score REAL, sector_tilt TEXT, macro_expression_rank INTEGER, active_theses TEXT, narrative TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS foreign_intel_signals (symbol TEXT, local_ticker TEXT, date TEXT, market TEXT, language TEXT, source TEXT, url TEXT, title_original TEXT, title_translated TEXT, sentiment REAL, relevance_score REAL, key_themes TEXT, mentioned_tickers TEXT, bullish_for TEXT, bearish_for TEXT, article_summary TEXT, translation_method TEXT, char_count_translated INTEGER, PRIMARY KEY (symbol, date, source))""",
            """CREATE TABLE IF NOT EXISTS foreign_intel_url_cache (url TEXT PRIMARY KEY, fetched_date TEXT, content TEXT, scraped_at TEXT, status TEXT)""",
            """CREATE TABLE IF NOT EXISTS foreign_ticker_map (local_ticker TEXT PRIMARY KEY, adr_ticker TEXT, company_name_local TEXT, company_name_english TEXT, market TEXT, sector TEXT, in_universe INTEGER DEFAULT 1)""",
            """CREATE TABLE IF NOT EXISTS research_signals (symbol TEXT, date TEXT, source TEXT, url TEXT, title TEXT, sentiment REAL, relevance_score REAL, key_themes TEXT, mentioned_tickers TEXT, bullish_for TEXT, bearish_for TEXT, article_summary TEXT, signal_type TEXT, score REAL, details TEXT, PRIMARY KEY (symbol, date, source))""",
            """CREATE TABLE IF NOT EXISTS research_url_cache (url TEXT PRIMARY KEY, fetched_date TEXT, content TEXT, scraped_at TEXT, status TEXT)""",
            """CREATE TABLE IF NOT EXISTS news_displacement (symbol TEXT, date TEXT, news_headline TEXT, news_source TEXT, news_url TEXT, materiality_score REAL, expected_direction TEXT, expected_magnitude REAL, actual_price_change_1d REAL, actual_price_change_3d REAL, displacement_score REAL, time_horizon TEXT, order_type TEXT, affected_tickers TEXT, confidence REAL, narrative TEXT, status TEXT DEFAULT 'active', details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS reddit_signals (symbol TEXT, date TEXT, subreddit TEXT, mention_count INTEGER, sentiment REAL, score REAL, PRIMARY KEY (symbol, date, subreddit))""",
            """CREATE TABLE IF NOT EXISTS alt_data_scores (symbol TEXT, date TEXT, source TEXT, score REAL, details TEXT, PRIMARY KEY (symbol, date, source))""",
            """CREATE TABLE IF NOT EXISTS alternative_data (date TEXT, source TEXT, indicator TEXT, value REAL, value_zscore REAL, affected_sectors TEXT, affected_tickers TEXT, signal_direction TEXT, signal_strength REAL, narrative TEXT, raw_data TEXT, PRIMARY KEY (date, source, indicator))""",
            """CREATE TABLE IF NOT EXISTS convergence_signals (symbol TEXT NOT NULL, date TEXT NOT NULL, convergence_score REAL NOT NULL, module_count INTEGER, conviction_level TEXT, forensic_blocked INTEGER DEFAULT 0, main_signal_score REAL, smartmoney_score REAL, worldview_score REAL, variant_score REAL, research_score REAL, reddit_score REAL, active_modules TEXT, narrative TEXT, news_displacement_score REAL, alt_data_score REAL, sector_expert_score REAL, foreign_intel_score REAL, pairs_score REAL, ma_score REAL, energy_intel_score REAL, prediction_markets_score REAL, pattern_options_score REAL, estimate_momentum_score REAL, ai_regulatory_score REAL, consensus_blindspots_score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS signal_outcomes (symbol TEXT NOT NULL, signal_date TEXT NOT NULL, conviction_level TEXT, convergence_score REAL, module_count INTEGER, active_modules TEXT, regime_at_signal TEXT, sector TEXT, market_cap_bucket TEXT, entry_price REAL, price_1d REAL, return_1d REAL, price_5d REAL, return_5d REAL, price_10d REAL, return_10d REAL, price_20d REAL, return_20d REAL, price_30d REAL, return_30d REAL, price_60d REAL, return_60d REAL, price_90d REAL, return_90d REAL, hit_target INTEGER, hit_stop INTEGER, da_risk_score REAL, da_warning INTEGER DEFAULT 0, PRIMARY KEY (symbol, signal_date))""",
            """CREATE TABLE IF NOT EXISTS module_performance (report_date TEXT NOT NULL, module_name TEXT NOT NULL, regime TEXT DEFAULT 'all', sector TEXT DEFAULT 'all', total_signals INTEGER, win_count INTEGER, win_rate REAL, avg_return_1d REAL, avg_return_5d REAL, avg_return_10d REAL, avg_return_20d REAL, avg_return_30d REAL, avg_return_60d REAL, avg_return_90d REAL, sharpe_ratio REAL, max_drawdown REAL, observation_count INTEGER, confidence_interval_low REAL, confidence_interval_high REAL, PRIMARY KEY (report_date, module_name, regime, sector))""",
            """CREATE TABLE IF NOT EXISTS weight_history (date TEXT NOT NULL, regime TEXT NOT NULL, module_name TEXT NOT NULL, weight REAL NOT NULL, prior_weight REAL, reason TEXT, PRIMARY KEY (date, regime, module_name))""",
            """CREATE TABLE IF NOT EXISTS weight_optimizer_log (date TEXT NOT NULL, action TEXT NOT NULL, details TEXT, PRIMARY KEY (date, action))""",
            """CREATE TABLE IF NOT EXISTS sector_expert_signals (symbol TEXT, date TEXT, sector TEXT, score REAL, details TEXT, expert_type TEXT, sector_displacement_score REAL, consensus_narrative TEXT, variant_narrative TEXT, leading_indicators TEXT, conviction_level TEXT, direction TEXT, key_catalysts TEXT, narrative TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS pattern_scan (symbol TEXT, date TEXT, regime TEXT, regime_score REAL, vix_percentile REAL, sector_quadrant TEXT, rotation_score REAL, rs_ratio REAL, rs_momentum REAL, patterns_detected TEXT, pattern_score REAL, sr_proximity TEXT, volume_profile_score REAL, hurst_exponent REAL, mr_score REAL, momentum_score REAL, compression_score REAL, squeeze_active INTEGER, wyckoff_phase TEXT, wyckoff_confidence REAL, earnings_days_to_next INTEGER, vol_regime TEXT, pattern_scan_score REAL, layer_scores TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS pattern_options_signals (symbol TEXT, date TEXT, pattern_scan_score REAL, options_score REAL, pattern_options_score REAL, top_pattern TEXT, top_signal TEXT, narrative TEXT, status TEXT, score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS options_intel (symbol TEXT, date TEXT, atm_iv REAL, hv_20d REAL, iv_premium REAL, iv_rank REAL, iv_percentile REAL, expected_move_pct REAL, straddle_cost REAL, volume_pc_ratio REAL, oi_pc_ratio REAL, pc_signal TEXT, unusual_activity_count INTEGER, unusual_activity TEXT, unusual_direction_bias TEXT, skew_25d REAL, skew_direction TEXT, term_structure_signal TEXT, net_gex REAL, gamma_flip_level REAL, vanna_exposure REAL, max_pain REAL, put_wall REAL, call_wall REAL, dealer_regime TEXT, options_score REAL, put_call_ratio REAL, unusual_volume INTEGER, score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS variant_analysis (symbol TEXT, date TEXT, variant_score REAL, thesis TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS devils_advocate (symbol TEXT, date TEXT, bear_thesis TEXT, kill_scenario TEXT, historical_analog TEXT, risk_score REAL, bull_context TEXT, regime_at_signal TEXT, warning_flag INTEGER, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS transcript_analysis (symbol TEXT, date TEXT, quarter TEXT, score REAL, summary TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS letter_analysis (symbol TEXT, date TEXT, score REAL, summary TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS forensic_alerts (symbol TEXT, date TEXT, alert_type TEXT, severity TEXT, details TEXT, PRIMARY KEY (symbol, date, alert_type))""",
            """CREATE TABLE IF NOT EXISTS earnings_calendar (symbol TEXT, date TEXT, estimate REAL, actual REAL, surprise REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS pair_relationships (symbol_a TEXT, symbol_b TEXT, sector TEXT, correlation_60d REAL, correlation_120d REAL, cointegration_pvalue REAL, hedge_ratio REAL, half_life_days REAL, spread_mean REAL, spread_std REAL, last_updated TEXT, PRIMARY KEY (symbol_a, symbol_b))""",
            """CREATE TABLE IF NOT EXISTS pair_spreads (symbol_a TEXT, symbol_b TEXT, date TEXT, spread REAL, z_score REAL, PRIMARY KEY (symbol_a, symbol_b, date))""",
            """CREATE TABLE IF NOT EXISTS pair_signals (symbol_a TEXT, symbol_b TEXT, date TEXT, signal_type TEXT, z_score REAL, direction TEXT, details TEXT, sector TEXT, spread_zscore REAL, correlation_60d REAL, cointegration_pvalue REAL, hedge_ratio REAL, half_life_days REAL, pairs_score REAL, runner_symbol TEXT, runner_tech_score REAL, runner_fund_score REAL, narrative TEXT, status TEXT, PRIMARY KEY (symbol_a, symbol_b, date, signal_type))""",
            """CREATE TABLE IF NOT EXISTS ma_signals (symbol TEXT, date TEXT, ma_score REAL, target_profile_score REAL, rumor_score REAL, valuation_score REAL, balance_sheet_score REAL, growth_score REAL, smart_money_score REAL, consolidation_bonus REAL, mcap_multiplier REAL, sector_multiplier REAL, deal_stage TEXT, rumor_credibility INTEGER, acquirer_name TEXT, expected_premium_pct REAL, best_headline TEXT, narrative TEXT, status TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS ma_rumors (symbol TEXT, date TEXT, source TEXT, headline TEXT, credibility REAL, deal_stage TEXT, details TEXT, PRIMARY KEY (symbol, date, source))""",
            """CREATE TABLE IF NOT EXISTS insider_transactions (symbol TEXT, date TEXT, insider_name TEXT, title TEXT, transaction_type TEXT, shares REAL, value REAL, PRIMARY KEY (symbol, date, insider_name, transaction_type))""",
            """CREATE TABLE IF NOT EXISTS insider_signals (symbol TEXT, date TEXT, insider_score REAL, cluster_buy INTEGER, cluster_count INTEGER, large_csuite INTEGER, unusual_volume INTEGER, total_buy_value_30d REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS foreign_ticker_map (local_ticker TEXT, adr_ticker TEXT, company_name_local TEXT, company_name_english TEXT, market TEXT, sector TEXT, in_universe INTEGER DEFAULT 1, PRIMARY KEY (local_ticker))""",
            """CREATE TABLE IF NOT EXISTS economic_dashboard (indicator_id TEXT, date TEXT, category TEXT, name TEXT, value REAL, prev_value REAL, mom_change REAL, yoy_change REAL, zscore REAL, trend TEXT, signal TEXT, last_updated TEXT, PRIMARY KEY (indicator_id, date))""",
            """CREATE TABLE IF NOT EXISTS economic_heat_index (date TEXT PRIMARY KEY, heat_index REAL, improving_count INTEGER, deteriorating_count INTEGER, stable_count INTEGER, leading_count INTEGER, detail TEXT)""",
            """CREATE TABLE IF NOT EXISTS hl_price_snapshots (ticker TEXT, timestamp TEXT, mid_price REAL, deployer TEXT, PRIMARY KEY (ticker, timestamp, deployer))""",
            """CREATE TABLE IF NOT EXISTS hl_gap_signals (ticker TEXT, date TEXT, predicted_gap REAL, actual_gap REAL, signal_time TEXT, details TEXT, PRIMARY KEY (ticker, date))""",
            """CREATE TABLE IF NOT EXISTS hl_deployer_spreads (ticker TEXT, date TEXT, deployer_a TEXT, deployer_b TEXT, spread REAL, PRIMARY KEY (ticker, date, deployer_a, deployer_b))""",
            """CREATE TABLE IF NOT EXISTS prediction_market_signals (symbol TEXT, date TEXT, pm_score REAL, market_count INTEGER, net_impact REAL, status TEXT, narrative TEXT, sector TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS prediction_market_raw (market_id TEXT, date TEXT, question TEXT, impact_category TEXT, yes_probability REAL, volume REAL, liquidity REAL, direction TEXT, confidence REAL, specific_symbols TEXT, rationale TEXT, end_date TEXT, probability REAL, category TEXT, relevance TEXT, PRIMARY KEY (market_id, date))""",
            """CREATE TABLE IF NOT EXISTS world_macro_indicators (indicator TEXT, country TEXT, date TEXT, value REAL, source TEXT, PRIMARY KEY (indicator, country, date))""",
            """CREATE TABLE IF NOT EXISTS estimate_snapshots (symbol TEXT, date TEXT, eps_current REAL, eps_next REAL, rev_current REAL, rev_next REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS estimate_momentum_signals (symbol TEXT, date TEXT, em_score REAL, revision_velocity REAL, surprise_momentum REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS regulatory_signals (symbol TEXT, date TEXT, reg_score REAL, event_count INTEGER, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS regulatory_events (event_id TEXT, date TEXT, title TEXT, source TEXT, severity REAL, category TEXT, direction TEXT, jurisdiction TEXT, affected_symbols TEXT, details TEXT, PRIMARY KEY (event_id, date))""",
            """CREATE TABLE IF NOT EXISTS consensus_blindspot_signals (symbol TEXT, date TEXT, cbs_score REAL, gap_type TEXT, cycle_position TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS intelligence_reports (id SERIAL PRIMARY KEY, date TEXT, topic TEXT, report_type TEXT, content TEXT, metadata TEXT, topic_type TEXT, expert_type TEXT, regime TEXT, symbols_covered TEXT, report_html TEXT, report_markdown TEXT)""",
            """CREATE TABLE IF NOT EXISTS thematic_ideas (id SERIAL PRIMARY KEY, date TEXT, theme TEXT, symbols TEXT, score REAL, details TEXT)""",
            """CREATE TABLE IF NOT EXISTS ai_exec_signals (symbol TEXT, date TEXT, score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS ai_exec_investments (exec_name TEXT, exec_org TEXT, exec_prominence TEXT, activity_type TEXT, target_company TEXT, target_ticker TEXT, target_sector TEXT, investment_amount REAL, funding_round TEXT, is_public INTEGER, ipo_expected INTEGER, ipo_timeline TEXT, date_reported TEXT, confidence REAL, summary TEXT, source_url TEXT, source TEXT, raw_score REAL, scan_date TEXT, PRIMARY KEY (exec_name, target_company, date_reported))""",
            """CREATE TABLE IF NOT EXISTS ai_exec_url_cache (url TEXT PRIMARY KEY, fetched_date TEXT, content TEXT, scraped_at TEXT, status TEXT)""",
            """CREATE TABLE IF NOT EXISTS energy_intel_signals (symbol TEXT, date TEXT, energy_intel_score REAL, inventory_signal REAL, production_signal REAL, demand_signal REAL, trade_flow_signal REAL, global_balance_signal REAL, ticker_category TEXT, narrative TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS energy_eia_enhanced (series_id TEXT, date TEXT, value REAL, category TEXT, description TEXT, wow_change REAL, yoy_change REAL, PRIMARY KEY (series_id, date))""",
            """CREATE TABLE IF NOT EXISTS energy_supply_anomalies (id SERIAL PRIMARY KEY, date TEXT, anomaly_type TEXT, series_id TEXT, description TEXT, zscore REAL, severity TEXT, affected_tickers TEXT, details TEXT, status TEXT DEFAULT 'active', detected_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS energy_trade_flows (reporter TEXT, partner TEXT, commodity_code TEXT, period TEXT, trade_flow TEXT, value_usd REAL, quantity_kg REAL, last_updated TEXT, date TEXT, country TEXT, product TEXT, flow_type TEXT, value REAL, PRIMARY KEY (reporter, partner, commodity_code, period, trade_flow))""",
            """CREATE TABLE IF NOT EXISTS energy_seasonal_norms (series_id TEXT, week_of_year INTEGER, avg_value REAL, std_value REAL, min_value REAL, max_value REAL, sample_count INTEGER, last_updated TEXT, PRIMARY KEY (series_id, week_of_year))""",
            """CREATE TABLE IF NOT EXISTS energy_jodi_data (country TEXT, indicator TEXT, date TEXT, value REAL, unit TEXT, mom_change REAL, yoy_change REAL, last_updated TEXT, flow TEXT, product TEXT, PRIMARY KEY (country, indicator, date))""",
            """CREATE TABLE IF NOT EXISTS global_energy_benchmarks (benchmark_id TEXT, date TEXT, name TEXT, unit TEXT, region TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, last_updated TEXT, PRIMARY KEY (benchmark_id, date))""",
            """CREATE TABLE IF NOT EXISTS global_energy_curves (curve_id TEXT, date TEXT, months_out INTEGER, contract_ticker TEXT, price REAL, last_updated TEXT, PRIMARY KEY (curve_id, date, months_out))""",
            """CREATE TABLE IF NOT EXISTS global_energy_spreads (spread_id TEXT, date TEXT, name TEXT, value REAL, leg_a REAL, leg_b REAL, assessment TEXT, unit TEXT, last_updated TEXT, PRIMARY KEY (spread_id, date))""",
            """CREATE TABLE IF NOT EXISTS global_energy_carbon (market_id TEXT, date TEXT, source_ticker TEXT, price REAL, unit TEXT, last_updated TEXT, PRIMARY KEY (market_id, date))""",
            """CREATE TABLE IF NOT EXISTS global_energy_signals (symbol TEXT, date TEXT, gem_score REAL, category TEXT, term_structure_signal REAL, basis_signal REAL, crack_signal REAL, carbon_signal REAL, narrative TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS signal_conflicts (symbol TEXT, date TEXT, conflict_type TEXT, severity TEXT, description TEXT, module_a TEXT, module_a_score REAL, module_b TEXT, module_b_score REAL, score_gap REAL, PRIMARY KEY (symbol, date, conflict_type))""",
            """CREATE TABLE IF NOT EXISTS thesis_snapshots (date TEXT, thesis TEXT, direction TEXT, confidence REAL, affected_sectors TEXT, PRIMARY KEY (date, thesis))""",
            """CREATE TABLE IF NOT EXISTS thesis_alerts (date TEXT, thesis TEXT, alert_type TEXT, severity TEXT, description TEXT, affected_symbols TEXT, lookback_days INTEGER, old_state TEXT, new_state TEXT, PRIMARY KEY (date, thesis, alert_type))""",
            """CREATE TABLE IF NOT EXISTS earnings_transcripts (symbol TEXT NOT NULL, date TEXT NOT NULL, quarter TEXT, filing_url TEXT, word_count INTEGER, sentiment REAL, hedging_ratio REAL, confidence_ratio REAL, key_phrases TEXT, PRIMARY KEY (symbol, quarter))""",
            """CREATE TABLE IF NOT EXISTS earnings_nlp_scores (symbol TEXT NOT NULL, date TEXT NOT NULL, earnings_nlp_score REAL, sentiment_delta REAL, hedging_delta REAL, guidance_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS gov_intel_raw (symbol TEXT NOT NULL, date TEXT NOT NULL, source TEXT NOT NULL, event_type TEXT NOT NULL, severity REAL, details TEXT, PRIMARY KEY (symbol, date, source, event_type))""",
            """CREATE TABLE IF NOT EXISTS gov_intel_scores (symbol TEXT NOT NULL, date TEXT NOT NULL, gov_intel_score REAL, warn_score REAL, osha_score REAL, epa_score REAL, fcc_score REAL, lobbying_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS labor_intel_raw (symbol TEXT NOT NULL, date TEXT NOT NULL, source TEXT NOT NULL, metric TEXT NOT NULL, value REAL, details TEXT, PRIMARY KEY (symbol, date, source, metric))""",
            """CREATE TABLE IF NOT EXISTS labor_intel_scores (symbol TEXT NOT NULL, date TEXT NOT NULL, labor_intel_score REAL, h1b_score REAL, hiring_score REAL, morale_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS supply_chain_raw (date TEXT NOT NULL, source TEXT NOT NULL, metric TEXT NOT NULL, value REAL, sector TEXT, details TEXT, PRIMARY KEY (date, source, metric))""",
            """CREATE TABLE IF NOT EXISTS supply_chain_scores (symbol TEXT NOT NULL, date TEXT NOT NULL, supply_chain_score REAL, rail_score REAL, shipping_score REAL, trucking_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS digital_exhaust_raw (symbol TEXT NOT NULL, date TEXT NOT NULL, source TEXT NOT NULL, metric TEXT NOT NULL, value REAL, prior_value REAL, details TEXT, PRIMARY KEY (symbol, date, source, metric))""",
            """CREATE TABLE IF NOT EXISTS digital_exhaust_scores (symbol TEXT NOT NULL, date TEXT NOT NULL, digital_exhaust_score REAL, app_score REAL, github_score REAL, pricing_score REAL, domain_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS pharma_intel_raw (symbol TEXT NOT NULL, date TEXT NOT NULL, source TEXT NOT NULL, metric TEXT NOT NULL, value REAL, details TEXT, PRIMARY KEY (symbol, date, source, metric))""",
            """CREATE TABLE IF NOT EXISTS pharma_intel_scores (symbol TEXT NOT NULL, date TEXT NOT NULL, pharma_intel_score REAL, trial_velocity_score REAL, stage_shift_score REAL, cms_score REAL, rx_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS stress_test_results (date TEXT, scenario TEXT, scenario_name TEXT, portfolio_impact_pct REAL, position_count INTEGER, position_details TEXT, worst_hit TEXT, best_positioned TEXT, PRIMARY KEY (date, scenario))""",
            """CREATE TABLE IF NOT EXISTS concentration_risk (date TEXT PRIMARY KEY, hhi REAL, concentration_level TEXT, top_sector TEXT, top_sector_pct REAL, details TEXT)""",
            """CREATE TABLE IF NOT EXISTS cross_asset_opportunities (symbol TEXT, date TEXT, asset_class TEXT, sector TEXT, opportunity_score REAL, technical_score REAL, fundamental_score REAL, momentum_5d REAL, momentum_20d REAL, momentum_60d REAL, regime_fit_score REAL, relative_value_rank REAL, is_fat_pitch INTEGER DEFAULT 0, fat_pitch_reason TEXT, conviction TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS signal_ic_results (module TEXT, signal_date TEXT, horizon_days INTEGER, ic_value REAL, pvalue REAL, n_stocks INTEGER, regime TEXT, PRIMARY KEY (module, signal_date, horizon_days, regime))""",
            """CREATE TABLE IF NOT EXISTS module_ic_summary (module TEXT, regime TEXT, horizon_days INTEGER, mean_ic REAL, std_ic REAL, information_ratio REAL, ic_positive_pct REAL, n_dates INTEGER, avg_n_stocks REAL, ci_low REAL, ci_high REAL, is_significant INTEGER, pvalue REAL, PRIMARY KEY (module, regime, horizon_days))""",
            """CREATE TABLE IF NOT EXISTS narrative_signals (narrative_id TEXT, date TEXT, narrative_name TEXT, strength_score REAL, crowding_score REAL, opportunity_score REAL, maturity TEXT, best_expression TEXT, avoid TEXT, macro_confirmations INTEGER, asset_confirmations INTEGER, details TEXT, PRIMARY KEY (narrative_id, date))""",
            """CREATE TABLE IF NOT EXISTS narrative_asset_map (narrative_id TEXT, symbol TEXT, date TEXT, asset_class TEXT, role TEXT, quality_score REAL, timing_score REAL, crowding_score REAL, combined_score REAL, PRIMARY KEY (narrative_id, symbol, date))""",
            """CREATE TABLE IF NOT EXISTS stress_backtest_results (crisis TEXT, sector_etf TEXT, sector TEXT, peak_date TEXT, trough_date TEXT, peak_price REAL, trough_price REAL, actual_drawdown REAL, assumed_drawdown REAL, calibration_error REAL, PRIMARY KEY (crisis, sector_etf))""",
            """CREATE TABLE IF NOT EXISTS stress_calibration (scenario TEXT, sector TEXT, assumed_impact REAL, calibrated_impact REAL, source_crisis TEXT, calibration_date TEXT, PRIMARY KEY (scenario, sector))""",
            """CREATE TABLE IF NOT EXISTS funnel_overrides (symbol TEXT, stage TEXT, action TEXT, reason TEXT, created_at TEXT DEFAULT NOW(), updated_at TEXT DEFAULT NOW(), expires_at TEXT, PRIMARY KEY (symbol, stage))""",
            """CREATE TABLE IF NOT EXISTS journal_entries (id SERIAL PRIMARY KEY, portfolio_id INTEGER, symbol TEXT NOT NULL, entry_type TEXT, content TEXT, convergence_snapshot TEXT, created_at TEXT DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS asset_class_signals (asset_class TEXT, date TEXT, proxy_symbol TEXT, regime_signal TEXT, score REAL, rationale TEXT, details TEXT, PRIMARY KEY (asset_class, date))""",
            """CREATE TABLE IF NOT EXISTS funnel_snapshot (date TEXT, run_id TEXT DEFAULT NOW(), universe_count INTEGER, sector_passed INTEGER, sector_flagged INTEGER, technical_passed INTEGER, technical_flagged INTEGER, conviction_high INTEGER, conviction_notable INTEGER, conviction_watch INTEGER, actionable_count INTEGER, PRIMARY KEY (date, run_id))""",
            """CREATE TABLE IF NOT EXISTS gate_results (symbol TEXT, date TEXT, gate_0 INTEGER DEFAULT 1, gate_1 INTEGER, gate_2 INTEGER, gate_3 INTEGER, gate_4 INTEGER, gate_5 INTEGER, gate_6 INTEGER, gate_7 INTEGER, gate_8 INTEGER, gate_9 INTEGER, gate_10 INTEGER, last_gate_passed INTEGER, fail_reason TEXT, asset_class TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS gate_overrides (symbol TEXT, gate INTEGER, direction TEXT, reason TEXT, expires TEXT, created_at TEXT DEFAULT NOW(), PRIMARY KEY (symbol, gate))""",
            """CREATE TABLE IF NOT EXISTS gate_run_history (run_id TEXT PRIMARY KEY, date TEXT, total_assets INTEGER, gate_1_passed INTEGER, gate_2_passed INTEGER, gate_3_passed INTEGER, gate_4_passed INTEGER, gate_5_passed INTEGER, gate_6_passed INTEGER, gate_7_passed INTEGER, gate_8_passed INTEGER, gate_9_passed INTEGER, gate_10_passed INTEGER, run_time_seconds REAL)""",
            """CREATE TABLE IF NOT EXISTS fmp_short_interest (symbol TEXT, date TEXT, short_interest REAL, short_float_pct REAL, days_to_cover REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS fmp_analyst_data (symbol TEXT, date TEXT, analyst_count INTEGER, strong_buy INTEGER, buy INTEGER, hold INTEGER, sell INTEGER, strong_sell INTEGER, consensus TEXT, price_target REAL, price_target_high REAL, price_target_low REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS fmp_dcf (symbol TEXT, date TEXT, dcf_value REAL, stock_price REAL, upside_pct REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS fmp_institutional (symbol TEXT, date TEXT, institutional_pct REAL, institution_count INTEGER, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS stocktwits_sentiment (symbol TEXT, date TEXT, bull_pct REAL, bear_pct REAL, msg_count INTEGER, sentiment_score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS coingecko_data (asset TEXT, date TEXT, price REAL, volume REAL, market_cap REAL, dominance_pct REAL, fear_greed_idx REAL, price_change_24h REAL, price_change_7d REAL, PRIMARY KEY (asset, date))""",
            """CREATE TABLE IF NOT EXISTS edgar_insider_raw (accession TEXT, owner_name TEXT, symbol TEXT, date TEXT, title TEXT, transaction_type TEXT, shares REAL, price REAL, value REAL, shares_owned_after REAL, form_type TEXT, filing_url TEXT, PRIMARY KEY (accession, owner_name))""",
            """CREATE TABLE IF NOT EXISTS edgar_filing_metadata (accession TEXT PRIMARY KEY, symbol TEXT, date TEXT, form_type TEXT, filer_name TEXT, filing_url TEXT, description TEXT)""",
            """CREATE TABLE IF NOT EXISTS av_technical_indicators (symbol TEXT, date TEXT, rsi REAL, macd REAL, macd_signal REAL, macd_hist REAL, stoch_k REAL, stoch_d REAL, adx REAL, bb_upper REAL, bb_middle REAL, bb_lower REAL, bb_width REAL, obv REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS finra_short_interest (symbol TEXT, date TEXT, short_volume REAL, total_volume REAL, short_vol_ratio REAL, short_interest REAL, days_to_cover REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS aar_rail_raw (date TEXT, commodity_type TEXT, carloadings INTEGER, yoy_change REAL, details TEXT, PRIMARY KEY (date, commodity_type))""",
            """CREATE TABLE IF NOT EXISTS aar_rail_scores (symbol TEXT, date TEXT, aar_rail_score REAL, macro_score REAL, sector_score REAL, momentum_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS analyst_scores (symbol TEXT, date TEXT, consensus_grade TEXT, pt_upside_pct REAL, analyst_count INTEGER, strong_buy_pct REAL, sell_pct REAL, revision_score REAL, composite_score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS board_interlocks_scores (symbol TEXT, date TEXT, board_interlocks_score REAL, quality_score REAL, network_score REAL, governance_change_score REAL, independence_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS capital_flow_scores (symbol TEXT, date TEXT, inst_ownership_pct REAL, inst_change_qoq REAL, new_positions INTEGER, smart_manager_count INTEGER, etf_flow_score REAL, composite REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS catalyst_scores (symbol TEXT, date TEXT, catalyst_type TEXT, catalyst_strength REAL, days_to_event INTEGER, score REAL, catalyst_detail TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS energy_regime (date TEXT PRIMARY KEY, seasonal_regime TEXT, curve_regime TEXT, storage_regime TEXT, cot_regime TEXT, composite_regime TEXT, regime_score REAL, narrative TEXT)""",
            """CREATE TABLE IF NOT EXISTS energy_regulatory_signals (date TEXT, source TEXT, signal_type TEXT, headline TEXT, detail TEXT, affected_sectors TEXT, affected_tickers TEXT, impact_score REAL, PRIMARY KEY (date, source, headline))""",
            """CREATE TABLE IF NOT EXISTS energy_stress_scores (date TEXT, symbol TEXT, scenario TEXT, impact_score REAL, direction TEXT, magnitude TEXT, PRIMARY KEY (date, symbol, scenario))""",
            """CREATE TABLE IF NOT EXISTS epo_patents (company_name TEXT, symbol TEXT, date TEXT, filing_count INTEGER, grant_count INTEGER, tech_class TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS etherscan_signals (date TEXT PRIMARY KEY, avg_gas_gwei REAL, whale_tx_count INTEGER, exchange_inflow_eth REAL, exchange_outflow_eth REAL, net_exchange_flow_eth REAL, usdt_supply REAL, usdc_supply REAL, score REAL, signal_type TEXT)""",
            """CREATE TABLE IF NOT EXISTS nansen_signals (asset TEXT, date TEXT, smart_money_flow REAL, whale_net REAL, defi_tvl_change REAL, signal_type TEXT, score REAL, PRIMARY KEY (asset, date))""",
            """CREATE TABLE IF NOT EXISTS onchain_scores (asset TEXT, date TEXT, whale_net_score REAL, exchange_flow_score REAL, smart_money_score REAL, fear_greed_adjusted_score REAL, composite REAL, PRIMARY KEY (asset, date))""",
            """CREATE TABLE IF NOT EXISTS options_flow_scores (symbol TEXT, date TEXT, call_put_ratio REAL, iv_rank REAL, unusual_activity_flag INTEGER, flow_direction TEXT, dealer_regime TEXT, score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS patent_intel_raw (symbol TEXT, date TEXT, patent_count_90d INTEGER, prior_count_90d INTEGER, filing_velocity REAL, cpc_classes TEXT, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS patent_intel_scores (symbol TEXT, date TEXT, patent_intel_score REAL, velocity_score REAL, quality_score REAL, tech_category_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS ship_tracking_raw (date TEXT, source TEXT, metric TEXT, value REAL, details TEXT, PRIMARY KEY (date, source, metric))""",
            """CREATE TABLE IF NOT EXISTS ship_tracking_scores (symbol TEXT, date TEXT, ship_tracking_score REAL, bdi_score REAL, freight_score REAL, congestion_score REAL, tanker_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS ucc_filings_scores (symbol TEXT, date TEXT, ucc_filings_score REAL, sec_language_score REAL, news_score REAL, leverage_score REAL, details TEXT, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS usda_commodity_data (commodity TEXT, date TEXT, production REAL, stocks REAL, exports REAL, price REAL, score REAL, PRIMARY KEY (commodity, date))""",
            """CREATE TABLE IF NOT EXISTS short_interest_scores (symbol TEXT, date TEXT, short_float_pct REAL, days_to_cover REAL, short_interest_change REAL, squeeze_score REAL, direction TEXT, score REAL, PRIMARY KEY (symbol, date))""",
            """CREATE TABLE IF NOT EXISTS retail_sentiment_scores (symbol TEXT, date TEXT, bull_pct REAL, bear_pct REAL, stocktwits_score REAL, reddit_score REAL, volume_surge INTEGER, contrarian_flag INTEGER, score REAL, PRIMARY KEY (symbol, date))""",
        ]

        # Add columns for future migrations (IF NOT EXISTS is safe to re-run)
        alter_statements = [
            "ALTER TABLE stock_universe ADD COLUMN IF NOT EXISTS asset_class TEXT DEFAULT 'stock'",
            "ALTER TABLE market_breadth ADD COLUMN IF NOT EXISTS advance_decline_ratio REAL",
            "ALTER TABLE market_breadth ADD COLUMN IF NOT EXISTS pct_above_200dma REAL",
            "ALTER TABLE insider_signals ADD COLUMN IF NOT EXISTS cluster_count INTEGER",
            "ALTER TABLE insider_signals ADD COLUMN IF NOT EXISTS total_buy_value_30d REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS correlation_60d REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS correlation_120d REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS cointegration_pvalue REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS half_life_days REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS spread_mean REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS spread_std REAL",
            "ALTER TABLE pair_relationships ADD COLUMN IF NOT EXISTS last_updated TEXT",
            "ALTER TABLE devils_advocate ADD COLUMN IF NOT EXISTS killers TEXT",
            "ALTER TABLE gate_results ADD COLUMN IF NOT EXISTS entry_mode TEXT",
            # convergence_signals — 17 module score columns added in Phase 2 expansion
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS earnings_nlp_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS gov_intel_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS labor_intel_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS supply_chain_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS digital_exhaust_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS pharma_intel_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS aar_rail_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS ship_tracking_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS patent_intel_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS ucc_filings_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS board_interlocks_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS short_interest_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS retail_sentiment_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS onchain_intel_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS analyst_intel_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS options_flow_score REAL",
            "ALTER TABLE convergence_signals ADD COLUMN IF NOT EXISTS capital_flows_score REAL",
            # sector_expert_signals — expanded schema
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS expert_type TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS sector_displacement_score REAL",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS consensus_narrative TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS variant_narrative TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS leading_indicators TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS conviction_level TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS direction TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS key_catalysts TEXT",
            "ALTER TABLE sector_expert_signals ADD COLUMN IF NOT EXISTS narrative TEXT",
            # pair_signals — expanded schema
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS sector TEXT",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS spread_zscore REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS correlation_60d REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS cointegration_pvalue REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS hedge_ratio REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS half_life_days REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS pairs_score REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS runner_symbol TEXT",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS runner_tech_score REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS runner_fund_score REAL",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS narrative TEXT",
            "ALTER TABLE pair_signals ADD COLUMN IF NOT EXISTS status TEXT",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS asset_class TEXT",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS macro_score REAL",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_price REAL",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_loss REAL",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_price REAL",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS rr_ratio REAL",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS position_size_shares REAL",
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS position_size_dollars REAL",
            "ALTER TABLE technical_scores ADD COLUMN IF NOT EXISTS breakout_score REAL",
            "ALTER TABLE technical_scores ADD COLUMN IF NOT EXISTS relative_strength_score REAL",
            "ALTER TABLE technical_scores ADD COLUMN IF NOT EXISTS breadth_score REAL",
            "ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS entry_thesis TEXT",
            "ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS entry_convergence_snapshot TEXT",
            "ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS exit_price REAL",
            "ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS exit_date TEXT",
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS topic_type TEXT",
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS expert_type TEXT",
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS regime TEXT",
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS symbols_covered TEXT",
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS report_html TEXT",
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS report_markdown TEXT",
            # Fix energy_supply_anomalies: ensure SERIAL id has a sequence default if missing
            """DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='energy_supply_anomalies' AND column_name='id'
                    AND column_default IS NULL
                ) THEN
                    CREATE SEQUENCE IF NOT EXISTS energy_supply_anomalies_id_seq;
                    ALTER TABLE energy_supply_anomalies ALTER COLUMN id SET DEFAULT nextval('energy_supply_anomalies_id_seq');
                    PERFORM setval('energy_supply_anomalies_id_seq', COALESCE((SELECT MAX(id) FROM energy_supply_anomalies), 0) + 1, false);
                END IF;
            END $$""",
            # Add unique constraint on natural key so ON CONFLICT works
            """DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='energy_supply_anomalies_natural_key'
                ) THEN
                    ALTER TABLE energy_supply_anomalies
                        ADD CONSTRAINT energy_supply_anomalies_natural_key
                        UNIQUE (date, series_id, anomaly_type);
                END IF;
            END $$""",
            # intelligence_reports: fix SERIAL id sequence if missing (broken old DDL)
            """DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='intelligence_reports' AND column_name='id'
                    AND column_default IS NULL
                ) THEN
                    CREATE SEQUENCE IF NOT EXISTS intelligence_reports_id_seq;
                    ALTER TABLE intelligence_reports ALTER COLUMN id SET DEFAULT nextval('intelligence_reports_id_seq');
                    PERFORM setval('intelligence_reports_id_seq', COALESCE((SELECT MAX(id) FROM intelligence_reports), 0) + 1, false);
                END IF;
            END $$""",
            # intelligence_reports: SERIAL PK on id, but upsert_many needs unique on natural key
            """DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname='intelligence_reports_natural_key'
                ) THEN
                    ALTER TABLE intelligence_reports
                        ADD CONSTRAINT intelligence_reports_natural_key
                        UNIQUE (topic, topic_type, expert_type, regime);
                END IF;
            END $$""",
            # ai_exec_signals: column added by upsert_many only when data exists; guarantee it
            "ALTER TABLE ai_exec_signals ADD COLUMN IF NOT EXISTS ai_exec_score REAL",
            "ALTER TABLE ai_exec_signals ADD COLUMN IF NOT EXISTS exec_count INTEGER",
            "ALTER TABLE ai_exec_signals ADD COLUMN IF NOT EXISTS top_exec TEXT",
            "ALTER TABLE ai_exec_signals ADD COLUMN IF NOT EXISTS top_activity TEXT",
            "ALTER TABLE ai_exec_signals ADD COLUMN IF NOT EXISTS sector_signal TEXT",
            "ALTER TABLE ai_exec_signals ADD COLUMN IF NOT EXISTS narrative TEXT",
            # energy_stress_scores: fix SERIAL id sequence if missing (broken old DDL)
            """DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='energy_stress_scores' AND column_name='id'
                    AND column_default IS NULL
                ) THEN
                    CREATE SEQUENCE IF NOT EXISTS energy_stress_scores_id_seq;
                    ALTER TABLE energy_stress_scores ALTER COLUMN id SET DEFAULT nextval('energy_stress_scores_id_seq');
                    PERFORM setval('energy_stress_scores_id_seq', COALESCE((SELECT MAX(id) FROM energy_stress_scores), 0) + 1, false);
                END IF;
            END $$""",
            # energy_stress_scores: table may have SERIAL id PK from old DDL; add UNIQUE on natural key
            """DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname='energy_stress_scores_natural_key'
                ) THEN
                    ALTER TABLE energy_stress_scores
                        ADD CONSTRAINT energy_stress_scores_natural_key
                        UNIQUE (date, symbol, scenario);
                END IF;
            END $$""",
            # Bulk fix: repair broken SERIAL id sequences for tables created by old SQLite DDL
            *[f"""DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='{t}' AND column_name='id' AND column_default IS NULL
                ) THEN
                    CREATE SEQUENCE IF NOT EXISTS {t}_id_seq;
                    ALTER TABLE {t} ALTER COLUMN id SET DEFAULT nextval('{t}_id_seq');
                    PERFORM setval('{t}_id_seq', COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false);
                END IF;
            END $$""" for t in (
                'cot_energy_positions','eia_storage_surprise','entso_gas_flows',
                'eu_gas_storage','journal_entries','lng_terminal_utilization',
                'portfolio','thematic_ideas',
            )],
            # energy_regime: fix SERIAL id sequence if missing
            """DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='energy_regime' AND column_name='id'
                    AND column_default IS NULL
                ) THEN
                    CREATE SEQUENCE IF NOT EXISTS energy_regime_id_seq;
                    ALTER TABLE energy_regime ALTER COLUMN id SET DEFAULT nextval('energy_regime_id_seq');
                    PERFORM setval('energy_regime_id_seq', COALESCE((SELECT MAX(id) FROM energy_regime), 0) + 1, false);
                END IF;
            END $$""",
            # energy_regime: add UNIQUE on date for upsert_many
            """DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname='energy_regime_date_key'
                ) THEN
                    ALTER TABLE energy_regime
                        ADD CONSTRAINT energy_regime_date_key
                        UNIQUE (date);
                END IF;
            END $$""",
        ]

        for stmt in statements:
            try:
                cur.execute("SAVEPOINT _init_stmt")
                cur.execute(stmt)
                cur.execute("RELEASE SAVEPOINT _init_stmt")
            except psycopg2.errors.DuplicateTable:
                cur.execute("ROLLBACK TO SAVEPOINT _init_stmt")
            except psycopg2.errors.UniqueViolation:
                # Stale composite row type from a previous partial run — safe to skip
                cur.execute("ROLLBACK TO SAVEPOINT _init_stmt")
        for stmt in alter_statements:
            try:
                cur.execute("SAVEPOINT _init_stmt")
                cur.execute(stmt)
                cur.execute("RELEASE SAVEPOINT _init_stmt")
            except (psycopg2.errors.DuplicateObject, psycopg2.errors.DuplicateTable,
                    psycopg2.errors.UniqueViolation):
                cur.execute("ROLLBACK TO SAVEPOINT _init_stmt")

        conn.commit()
    finally:
        cur.close()
        _release(conn)


def sync_local_from_neon(force: bool = False):
    """Sync LOCAL_TABLES data from Neon to SQLite when SQLite is stale.

    Called at pipeline start to handle the case where yesterday's pipeline wrote
    to Neon (before LOCAL_TABLES routing was active) and SQLite is behind.
    Only syncs tables where Neon has more-recent data than SQLite.
    """
    import sqlite3 as _sq3
    from datetime import date as _date

    today = _date.today().isoformat()
    sq = _sq3.connect(_SQLITE_PATH, timeout=60)

    # --- price_data ---
    try:
        sq_max = sq.execute("SELECT MAX(date) FROM price_data").fetchone()[0]
        if force or (sq_max is None or sq_max < today):
            pg = get_conn()
            with pg.cursor() as cur:
                cutoff = sq_max if sq_max else (str(_date.today().replace(month=1, day=1)))
                cur.execute(
                    "SELECT symbol, date, open, high, low, close, volume, asset_class "
                    "FROM price_data WHERE date > %s ORDER BY date",
                    [cutoff],
                )
                rows = cur.fetchall()
            _release(pg)
            if rows:
                sq.executemany(
                    "INSERT OR REPLACE INTO price_data "
                    "(symbol, date, open, high, low, close, volume, asset_class) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    rows,
                )
                sq.commit()
                print(f"  [sync] price_data: pulled {len(rows)} rows from Neon (Neon>{sq_max})")
    except Exception as e:
        print(f"  [sync] price_data sync skipped: {e}")

    # --- macro_indicators ---
    try:
        sq_max = sq.execute("SELECT MAX(date) FROM macro_indicators").fetchone()[0]
        if force or (sq_max is None or sq_max < today):
            pg = get_conn()
            with pg.cursor() as cur:
                cur.execute(
                    "SELECT indicator_id, date, value FROM macro_indicators "
                    "WHERE date > %s ORDER BY date",
                    [sq_max or "2020-01-01"],
                )
                rows = cur.fetchall()
            _release(pg)
            if rows:
                sq.executemany(
                    "INSERT OR REPLACE INTO macro_indicators (indicator_id, date, value) VALUES (?,?,?)",
                    rows,
                )
                sq.commit()
                print(f"  [sync] macro_indicators: pulled {len(rows)} rows from Neon")
    except Exception as e:
        print(f"  [sync] macro_indicators sync skipped: {e}")

    # --- insider_signals ---
    try:
        sq_max = sq.execute("SELECT MAX(date) FROM insider_signals").fetchone()[0]
        if force or (sq_max is None or sq_max < today):
            pg = get_conn()
            with pg.cursor() as cur:
                cur.execute(
                    "SELECT symbol, date, insider_score, cluster_buy, cluster_count, "
                    "large_buys_count, total_buy_value_30d, total_sell_value_30d, "
                    "unusual_volume_flag, top_buyer, narrative "
                    "FROM insider_signals WHERE date > %s",
                    [sq_max or "2020-01-01"],
                )
                rows = cur.fetchall()
            _release(pg)
            if rows:
                sq.executemany(
                    "INSERT OR REPLACE INTO insider_signals "
                    "(symbol, date, insider_score, cluster_buy, cluster_count, "
                    "large_buys_count, total_buy_value_30d, total_sell_value_30d, "
                    "unusual_volume_flag, top_buyer, narrative) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                sq.commit()
                print(f"  [sync] insider_signals: pulled {len(rows)} rows from Neon")
    except Exception as e:
        print(f"  [sync] insider_signals sync skipped: {e}")

    sq.close()



def _to_pg(sql):
    """Convert SQLite SQL to Postgres-compatible SQL."""
    # SQLite strftime('%fmt', col) → TO_CHAR(col::date, 'pg_fmt')
    # Must run BEFORE %-escaping since strftime format strings contain literal %
    _sqlite_to_pg_fmt = {'%Y': 'YYYY', '%m': 'MM', '%d': 'DD', '%H': 'HH24', '%M': 'MI', '%S': 'SS'}
    def _strftime_fn(m):
        fmt = m.group(1)
        col = m.group(2)
        pg_fmt = fmt
        for sf, pf in _sqlite_to_pg_fmt.items():
            pg_fmt = pg_fmt.replace(sf, pf)
        return f"TO_CHAR({col}::date, '{pg_fmt}')"
    sql = _re.sub(r"strftime\s*\(\s*'([^']+)'\s*,\s*([^)]+)\)", _strftime_fn, sql, flags=_re.IGNORECASE)

    # Escape literal % in LIKE patterns (e.g. '%BUY%') but not existing %s placeholders.
    # Replace % not followed by 's' with %%.
    sql = _re.sub(r'%(?!s)', '%%', sql)
    # Parameter placeholders
    sql = sql.replace("?", "%s")
    # AUTOINCREMENT → SERIAL (DDL)
    sql = _re.sub(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', 'SERIAL PRIMARY KEY', sql, flags=_re.IGNORECASE)
    sql = sql.replace("AUTOINCREMENT", "")
    # INSERT OR IGNORE → INSERT INTO ... ON CONFLICT DO NOTHING
    def _insert_ignore(m):
        return 'INSERT INTO'
    has_ignore = bool(_re.search(r'INSERT\s+OR\s+IGNORE\s+INTO', sql, flags=_re.IGNORECASE))
    sql = _re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=_re.IGNORECASE)

    # INSERT OR REPLACE → INSERT INTO ... ON CONFLICT (pk) DO UPDATE SET ...
    def _insert_replace(m):
        return 'INSERT INTO'
    has_replace = bool(_re.search(r'INSERT\s+OR\s+REPLACE\s+INTO', sql, flags=_re.IGNORECASE))
    sql = _re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO', sql, flags=_re.IGNORECASE)
    # SQLite date arithmetic: date('now', '-N days') → CURRENT_DATE - N
    def _date_fn(m):
        arg = m.group(1).strip().strip("'\"")
        # Parameterized offset: date('now', ? || ' days') — ? already converted to %s.
        # PostgreSQL: (CURRENT_DATE + ('-30' || ' days')::interval)::text works correctly.
        if '%s' in arg:
            return "(CURRENT_DATE + (%s || ' days')::interval)::text"
        if arg.startswith('-') or arg.startswith('+'):
            try:
                n = int(arg.split()[0])
                unit = arg.split()[1].rstrip('s') if len(arg.split()) > 1 else 'day'
                if n < 0:
                    return f"(CURRENT_DATE - INTERVAL '{-n} {unit}s')::text"
                else:
                    return f"(CURRENT_DATE + INTERVAL '{n} {unit}s')::text"
            except Exception:
                pass
        return 'CURRENT_DATE::text'
    sql = _re.sub(r"date\s*\(\s*'now'\s*,\s*([^)]+)\)", _date_fn, sql, flags=_re.IGNORECASE)
    sql = _re.sub(r"date\s*\(\s*'now'\s*\)", "CURRENT_DATE::text", sql, flags=_re.IGNORECASE)
    sql = _re.sub(r"datetime\s*\(\s*'now'\s*,\s*([^)]+)\)", _date_fn, sql, flags=_re.IGNORECASE)
    sql = _re.sub(r"datetime\s*\(\s*'now'\s*\)", "NOW()::text", sql, flags=_re.IGNORECASE)
    # julianday() → Postgres epoch-based equivalent (days since epoch)
    sql = _re.sub(r"julianday\s*\(\s*'now'\s*\)", "EXTRACT(EPOCH FROM NOW())/86400", sql, flags=_re.IGNORECASE)
    sql = _re.sub(r"julianday\s*\(([^)]+)\)", r"EXTRACT(EPOCH FROM \1::timestamp)/86400", sql, flags=_re.IGNORECASE)
    # SQLite substr-based date functions: substr(date, ...) used for month/year extraction
    # PostgreSQL equivalent: these usually work as-is since substr is standard SQL

    # GROUP_CONCAT → STRING_AGG (Postgres)
    sql = _re.sub(r"GROUP_CONCAT\s*\(([^,]+),\s*([^)]+)\)", r"STRING_AGG(\1, \2)", sql, flags=_re.IGNORECASE)

    # Append ON CONFLICT clause for INSERT OR IGNORE / INSERT OR REPLACE
    if has_ignore or has_replace:
        # Extract table name from INSERT INTO <table>
        tbl_m = _re.search(r'INSERT\s+INTO\s+(\w+)', sql, flags=_re.IGNORECASE)
        if tbl_m:
            table = tbl_m.group(1).lower()
            pk_cols = TABLE_PKS.get(table)
            if pk_cols and has_replace:
                # Extract column list from INSERT INTO table (col1, col2, ...)
                cols_m = _re.search(r'INSERT\s+INTO\s+\w+\s*\(([^)]+)\)', sql, flags=_re.IGNORECASE)
                if cols_m:
                    cols = [c.strip() for c in cols_m.group(1).split(',')]
                    non_pk = [c for c in cols if c not in pk_cols]
                    pk_str = ', '.join(pk_cols)
                    if non_pk:
                        updates = ', '.join(f'{c}=EXCLUDED.{c}' for c in non_pk)
                        sql = sql.rstrip('; \n') + f' ON CONFLICT ({pk_str}) DO UPDATE SET {updates}'
                    else:
                        sql = sql.rstrip('; \n') + f' ON CONFLICT ({pk_str}) DO NOTHING'
                else:
                    sql = sql.rstrip('; \n') + ' ON CONFLICT DO NOTHING'
            else:
                sql = sql.rstrip('; \n') + ' ON CONFLICT DO NOTHING'
        else:
            sql = sql.rstrip('; \n') + ' ON CONFLICT DO NOTHING'

    return sql


def query(sql, params=None):
    """Execute SQL and return list of dicts. Accepts both ? and %s placeholders.
    Automatically routes queries for LOCAL_TABLES to the local SQLite file."""
    if _extract_table(sql) in LOCAL_TABLES:
        return _sqlite_query(sql, params)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or [])  # _PgCursorWrapper.execute applies _to_pg
            return [dict(r) for r in cur.fetchall()]
    finally:
        _release(conn)


_sa_engine = None

def _get_sa_engine():
    global _sa_engine
    if _sa_engine is None:
        from sqlalchemy import create_engine, event
        engine = create_engine(
            _DATABASE_URL,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,          # ping before checkout — catches silent SSL drops
            pool_recycle=300,            # recycle connections every 5 min
            connect_args={               # TCP keepalives mirror the psycopg2 pool
                "keepalives": 1,
                "keepalives_idle": 60,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
        _sa_engine = engine
    return _sa_engine

def query_df(sql, params=None):
    """Execute SQL and return a pandas DataFrame.
    Routes LOCAL_TABLES to SQLite (same as query()); all others to Neon via psycopg2."""
    import pandas as _pd
    rows = query(sql, params)
    if not rows:
        return _pd.DataFrame()
    return _pd.DataFrame(rows)


def upsert_many(table, columns, rows):
    """INSERT ... ON CONFLICT (pk_cols) DO UPDATE SET ... for many rows.

    Routes LOCAL_TABLES writes to SQLite; all other tables go to Neon.
    For Neon tables: automatically filters to columns that exist in the Postgres
    table, adding new columns on-the-fly with ALTER TABLE when introduced.
    """
    if not rows:
        return
    if table in LOCAL_TABLES:
        def _coerce(v):
            t = type(v).__module__
            return v.item() if t == "numpy" else v
        coerced = [tuple(_coerce(v) for v in row) for row in rows]
        _sqlite_upsert(table, columns, coerced)
        return
    pk_cols = TABLE_PKS.get(table)
    if not pk_cols:
        raise ValueError(f"upsert_many: no PK defined for table '{table}'. Add to TABLE_PKS.")

    existing = _pg_columns(table)
    # Add any new columns the pipeline is trying to write (schema evolution)
    new_cols = [c for c in columns if c not in existing]
    if new_cols:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                for col in new_cols:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} TEXT")
            conn.commit()
        finally:
            _release(conn)
        _invalidate_col_cache(table)
        existing = _pg_columns(table)

    # Only write columns that exist in Postgres
    filtered = [c for c in columns if c in existing]
    if not filtered:
        return
    col_indices = [columns.index(c) for c in filtered]

    col_str = ", ".join(filtered)
    placeholders = ", ".join(["%s"] * len(filtered))
    conflict_cols = ", ".join(pk_cols)
    update_cols = [c for c in filtered if c not in pk_cols]
    if update_cols:
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        on_conflict = f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_str}"
    else:
        on_conflict = f"ON CONFLICT ({conflict_cols}) DO NOTHING"
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) {on_conflict}"

    def _coerce(v):
        """Convert numpy scalar types to native Python so psycopg2 serialises correctly."""
        t = type(v).__module__
        if t == "numpy":
            return v.item()
        return v

    filtered_rows = [tuple(_coerce(row[i]) for i in col_indices) for row in rows]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, filtered_rows)
        conn.commit()
    finally:
        _release(conn)
