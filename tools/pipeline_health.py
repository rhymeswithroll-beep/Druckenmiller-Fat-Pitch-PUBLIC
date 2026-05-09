"""Phase 5: Pipeline Data Health Monitor.

Runs after every pipeline. Checks 5 categories of data quality and writes
a structured report to the `pipeline_health` table. Surfaces in dashboard.

Checks:
  1. Freshness   — every key table should have today's data
  2. Coverage    — key tables should cover >85% of the stock universe
  3. Distribution — flag if any score column has <10 distinct values (uniformity problem)
  4. Sentinels   — count exact-default values (50.0, 2.0, 0) — flag if >20%
  5. Cross-table — variant/convergence consistency; analyst targets vs price proximity
"""

import json
import logging
from datetime import date, datetime
from tools.db import query, get_sqlite_conn

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# Tables that MUST have today's data after a full pipeline run
FRESHNESS_CHECKS = [
    ("signals",             "date"),
    ("technical_scores",    "date"),
    ("fundamental_scores",  "date"),
    ("convergence_signals", "date"),
    ("macro_scores",        "date"),
    ("market_breadth",      "date"),
]

# Tables that should cover most of the universe — (table, date_col, score_col)
COVERAGE_CHECKS = [
    ("signals",             "date",  None),
    ("technical_scores",    "date",  None),
    ("fundamental_scores",  "date",  None),
    ("convergence_signals", "date",  None),
]

# Score columns to check for distribution uniformity
DISTRIBUTION_CHECKS = [
    ("signals",             "date", "composite_score"),
    ("signals",             "date", "rr_ratio"),
    ("technical_scores",    "date", "total_score"),
    ("fundamental_scores",  "date", "total_score"),
    ("convergence_signals", "date", "convergence_score"),
]

# Sentinel values that indicate a pipeline failure (value, column, table)
SENTINEL_CHECKS = [
    ("signals",          "rr_ratio",         2.0),
    ("signals",          "composite_score",  50.0),
    ("technical_scores", "total_score",      50.0),
    ("fundamental_scores","total_score",     50.0),
    ("convergence_signals","convergence_score", 0.0),
]

# Minimum coverage threshold (fraction of universe)
MIN_COVERAGE = 0.85
# Minimum distinct value count for distribution check
MIN_DISTINCT_VALUES = 10
# Maximum sentinel fraction before flagging
MAX_SENTINEL_FRACTION = 0.20


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_query(sql, params=None):
    try:
        return query(sql, params or [])
    except Exception as e:
        logger.warning(f"Health check query failed: {e} | SQL: {sql[:80]}")
        return []


def _universe_count():
    rows = _safe_query("SELECT COUNT(*) as n FROM stock_universe")
    return rows[0]["n"] if rows else 903  # fallback


def _latest_date(table, date_col):
    rows = _safe_query(f"SELECT MAX({date_col}) as d FROM {table}")
    return rows[0]["d"] if rows else None


# ── Check 1: Freshness ─────────────────────────────────────────────────────────

def check_freshness(today_str: str) -> dict:
    """Return pass/warn/fail for each table based on whether today's data exists."""
    results = {}
    for table, date_col in FRESHNESS_CHECKS:
        latest = _latest_date(table, date_col)
        if latest is None:
            results[table] = {"status": "fail", "latest_date": None, "detail": "Table empty"}
        elif latest == today_str:
            results[table] = {"status": "pass", "latest_date": latest, "detail": "Fresh"}
        else:
            # Calculate days stale
            try:
                delta = (date.fromisoformat(today_str) - date.fromisoformat(latest)).days
            except Exception:
                delta = -1
            if delta <= 1:
                results[table] = {"status": "warn", "latest_date": latest, "detail": f"1 day stale"}
            else:
                results[table] = {"status": "fail", "latest_date": latest, "detail": f"{delta} days stale"}
    return results


# ── Check 2: Coverage ──────────────────────────────────────────────────────────

def check_coverage(today_str: str, universe_n: int) -> dict:
    """Return coverage fraction for each key table (today's data only)."""
    results = {}
    for table, date_col, _ in COVERAGE_CHECKS:
        latest = _latest_date(table, date_col)
        if not latest:
            results[table] = {"status": "fail", "count": 0, "fraction": 0.0, "detail": "No data"}
            continue

        rows = _safe_query(
            f"SELECT COUNT(DISTINCT symbol) as n FROM {table} WHERE {date_col} = ?",
            [latest]
        )
        count = rows[0]["n"] if rows else 0
        fraction = count / universe_n if universe_n > 0 else 0.0
        if fraction >= MIN_COVERAGE:
            status = "pass"
        elif fraction >= 0.50:
            status = "warn"
        else:
            status = "fail"
        results[table] = {
            "status": status,
            "count": count,
            "universe": universe_n,
            "fraction": round(fraction, 3),
            "detail": f"{count}/{universe_n} ({fraction:.0%})",
        }
    return results


# ── Check 3: Distribution ──────────────────────────────────────────────────────

def check_distribution(today_str: str) -> dict:
    """Flag score columns with suspiciously few distinct values (sentinel uniformity)."""
    results = {}
    for table, date_col, score_col in DISTRIBUTION_CHECKS:
        key = f"{table}.{score_col}"
        latest = _latest_date(table, date_col)
        if not latest:
            results[key] = {"status": "fail", "distinct_values": 0, "detail": "No data"}
            continue

        rows = _safe_query(
            f"SELECT COUNT(DISTINCT ROUND({score_col}, 1)) as n FROM {table} WHERE {date_col} = ?",
            [latest]
        )
        distinct = rows[0]["n"] if rows else 0
        if distinct >= MIN_DISTINCT_VALUES:
            status = "pass"
        elif distinct >= 5:
            status = "warn"
        else:
            status = "fail"
        results[key] = {
            "status": status,
            "distinct_values": distinct,
            "detail": f"{distinct} distinct values (need ≥{MIN_DISTINCT_VALUES})",
        }
    return results


# ── Check 4: Sentinels ─────────────────────────────────────────────────────────

def check_sentinels(today_str: str) -> dict:
    """Count rows stuck at exact default values — signals a pipeline failure."""
    results = {}
    for table, col, sentinel in SENTINEL_CHECKS:
        key = f"{table}.{col}={sentinel}"
        latest = _latest_date(table, "date")
        if not latest:
            results[key] = {"status": "fail", "sentinel_fraction": 1.0, "detail": "No data"}
            continue

        total_rows = _safe_query(
            f"SELECT COUNT(*) as n FROM {table} WHERE date = ?", [latest]
        )
        total = total_rows[0]["n"] if total_rows else 0
        if total == 0:
            results[key] = {"status": "fail", "sentinel_fraction": 1.0, "detail": "No rows"}
            continue

        sentinel_rows = _safe_query(
            f"SELECT COUNT(*) as n FROM {table} WHERE date = ? AND ABS({col} - ?) < 0.001",
            [latest, sentinel]
        )
        sentinel_count = sentinel_rows[0]["n"] if sentinel_rows else 0
        fraction = sentinel_count / total
        if fraction <= MAX_SENTINEL_FRACTION:
            status = "pass"
        elif fraction <= 0.50:
            status = "warn"
        else:
            status = "fail"
        results[key] = {
            "status": status,
            "sentinel_count": sentinel_count,
            "total": total,
            "sentinel_fraction": round(fraction, 3),
            "detail": f"{sentinel_count}/{total} ({fraction:.0%}) = {sentinel}",
        }
    return results


# ── Check 5: Cross-Table Consistency ──────────────────────────────────────────

def check_cross_table(today_str: str) -> dict:
    """Check consistency between related tables."""
    results = {}

    # 5a: Analyst targets coverage — how many stocks have targets loaded
    target_rows = _safe_query(
        "SELECT COUNT(DISTINCT symbol) as n FROM fundamentals WHERE metric = 'analyst_target_consensus'"
    )
    target_count = target_rows[0]["n"] if target_rows else 0
    universe_n = _universe_count()
    target_frac = target_count / universe_n if universe_n > 0 else 0.0
    results["analyst_targets_coverage"] = {
        "status": "pass" if target_frac >= 0.60 else ("warn" if target_frac >= 0.30 else "fail"),
        "count": target_count,
        "fraction": round(target_frac, 3),
        "detail": f"{target_count}/{universe_n} stocks have analyst targets ({target_frac:.0%})",
    }

    # 5b: Signals with R:R > 0 (0 means stop = entry, broken calculation)
    signal_latest = _latest_date("signals", "date")
    if signal_latest:
        rr_rows = _safe_query(
            "SELECT COUNT(*) as total, SUM(CASE WHEN rr_ratio > 0 THEN 1 ELSE 0 END) as valid "
            "FROM signals WHERE date = ?", [signal_latest]
        )
        if rr_rows:
            total = rr_rows[0]["total"] or 0
            valid = rr_rows[0]["valid"] or 0
            frac = valid / total if total > 0 else 0.0
            results["signals_valid_rr"] = {
                "status": "pass" if frac >= 0.90 else ("warn" if frac >= 0.70 else "fail"),
                "valid_count": valid,
                "total": total,
                "fraction": round(frac, 3),
                "detail": f"{valid}/{total} signals have valid R:R ({frac:.0%})",
            }

    # 5c: Foreign intel — any signals in last 7 days?
    fi_rows = _safe_query(
        "SELECT COUNT(*) as n FROM foreign_intel_signals "
        "WHERE date >= date('now', '-7 days') AND symbol != 'UNMAPPED'"
    )
    fi_count = fi_rows[0]["n"] if fi_rows else 0
    results["foreign_intel_7d"] = {
        "status": "pass" if fi_count >= 5 else ("warn" if fi_count >= 1 else "fail"),
        "count": fi_count,
        "detail": f"{fi_count} foreign intel signals in last 7 days",
    }

    # 5d: Variant perception coverage (should analyze 30%+ of gated universe)
    vp_rows = _safe_query(
        "SELECT COUNT(DISTINCT symbol) as n FROM variant_analysis "
        "WHERE date >= date('now', '-3 days')"
    )
    vp_count = vp_rows[0]["n"] if vp_rows else 0
    results["variant_perception_3d"] = {
        "status": "pass" if vp_count >= 100 else ("warn" if vp_count >= 30 else "fail"),
        "count": vp_count,
        "detail": f"{vp_count} stocks analyzed by Variant Perception in last 3 days",
    }

    return results


# ── Aggregate & Score ──────────────────────────────────────────────────────────

def _count_statuses(checks: dict) -> tuple[int, int, int]:
    """Return (pass_count, warn_count, fail_count) from a checks dict."""
    p = w = f = 0
    for v in checks.values():
        s = v.get("status", "fail")
        if s == "pass":
            p += 1
        elif s == "warn":
            w += 1
        else:
            f += 1
    return p, w, f


def _overall_status(all_checks: dict) -> str:
    total_fail = sum(
        sum(1 for v in group.values() if v.get("status") == "fail")
        for group in all_checks.values()
    )
    total_warn = sum(
        sum(1 for v in group.values() if v.get("status") == "warn")
        for group in all_checks.values()
    )
    if total_fail > 0:
        return "fail"
    elif total_warn > 0:
        return "warn"
    return "pass"


# ── DB Init ────────────────────────────────────────────────────────────────────

def _init_health_table():
    """Create the pipeline_health table if it doesn't exist."""
    conn = get_sqlite_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_health (
                run_date        TEXT PRIMARY KEY,
                overall_status  TEXT,
                freshness_json  TEXT,
                coverage_json   TEXT,
                distribution_json TEXT,
                sentinels_json  TEXT,
                cross_table_json TEXT,
                summary_json    TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ── Main Entry Point ───────────────────────────────────────────────────────────

def run():
    """Run all health checks and persist results."""
    _init_health_table()
    today_str = date.today().isoformat()
    universe_n = _universe_count()

    print(f"\n{'─' * 60}")
    print(f"  PIPELINE HEALTH CHECK — {today_str}")
    print(f"  Universe: {universe_n} stocks")
    print(f"{'─' * 60}")

    # Run all checks
    freshness     = check_freshness(today_str)
    coverage      = check_coverage(today_str, universe_n)
    distribution  = check_distribution(today_str)
    sentinels     = check_sentinels(today_str)
    cross_table   = check_cross_table(today_str)

    all_checks = {
        "freshness":    freshness,
        "coverage":     coverage,
        "distribution": distribution,
        "sentinels":    sentinels,
        "cross_table":  cross_table,
    }

    # Summarize
    summary = {}
    total_issues = []
    for category, checks in all_checks.items():
        p, w, f = _count_statuses(checks)
        summary[category] = {"pass": p, "warn": w, "fail": f}
        for check_name, result in checks.items():
            status = result.get("status", "fail")
            if status in ("warn", "fail"):
                total_issues.append({
                    "category": category,
                    "check": check_name,
                    "status": status,
                    "detail": result.get("detail", ""),
                })

    overall = _overall_status(all_checks)
    summary["overall"] = overall
    summary["issue_count"] = len(total_issues)
    summary["issues"] = total_issues

    # Print report
    STATUS_ICON = {"pass": "✓", "warn": "⚠", "fail": "✗"}
    print(f"\n  Overall: {STATUS_ICON.get(overall, '?')} {overall.upper()}")
    print(f"  Issues:  {len(total_issues)} ({sum(1 for i in total_issues if i['status'] == 'fail')} fail, "
          f"{sum(1 for i in total_issues if i['status'] == 'warn')} warn)")

    for category, checks in all_checks.items():
        p, w, f = _count_statuses(checks)
        cat_status = "fail" if f > 0 else ("warn" if w > 0 else "pass")
        print(f"\n  [{category.upper()}] {STATUS_ICON.get(cat_status, '?')} "
              f"{p} pass / {w} warn / {f} fail")
        for check_name, result in checks.items():
            s = result.get("status", "fail")
            if s != "pass":
                print(f"    {STATUS_ICON.get(s, '?')} {check_name}: {result.get('detail', '')}")

    # Persist to DB
    conn = get_sqlite_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO pipeline_health
                (run_date, overall_status, freshness_json, coverage_json,
                 distribution_json, sentinels_json, cross_table_json, summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today_str,
            overall,
            json.dumps(freshness),
            json.dumps(coverage),
            json.dumps(distribution),
            json.dumps(sentinels),
            json.dumps(cross_table),
            json.dumps(summary),
        ))
        conn.commit()
    finally:
        conn.close()

    print(f"\n  Health check saved to pipeline_health table.")
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from tools.db import init_db
    init_db()
    run()
