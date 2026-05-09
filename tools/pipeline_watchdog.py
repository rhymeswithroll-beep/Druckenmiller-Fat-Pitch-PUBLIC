"""Pipeline Watchdog — Druckenmiller Alpha System.

Checks whether the daily pipeline ran successfully today. If it didn't,
or if the last pipeline completed with status 'fail', sends an email alert.

Designed to be called by Windows Task Scheduler once per day, a few hours
after the pipeline should have finished (e.g. 10:00 AM if pipeline runs at 6 AM).

Usage:
    python -m tools.pipeline_watchdog

Exit codes:
    0 — pipeline ran and is healthy (or warn — logged but no alert)
    1 — pipeline did NOT run today (alert fired)
    2 — pipeline ran but health status is 'fail' (alert fired)
"""

import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _send_alert(subject: str, body: str) -> bool:
    """Send alert email. Returns True if sent, False if skipped/failed."""
    try:
        from tools.config import SMTP_USER, SMTP_PASS, EMAIL_TO
    except ImportError:
        print("[watchdog] config not importable — skipping email")
        return False

    if not SMTP_USER or not SMTP_PASS or not EMAIL_TO:
        print("[watchdog] SMTP credentials not configured — skipping email")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(body, "plain"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())
        print(f"[watchdog] Alert sent to {EMAIL_TO}: {subject}")
        return True
    except Exception as e:
        print(f"[watchdog] Email failed: {e}")
        return False


def run() -> int:
    """Check pipeline health and alert if needed. Returns exit code."""
    from tools.db import init_db, get_sqlite_conn

    init_db()

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    conn = get_sqlite_conn()
    try:
        # Check if pipeline ran today
        row = conn.execute(
            "SELECT overall_status, summary_json, created_at FROM pipeline_health WHERE run_date = ?",
            (today,)
        ).fetchone()

        # Also check yesterday (in case watchdog runs early morning before today's run)
        if not row:
            row_yday = conn.execute(
                "SELECT overall_status, summary_json, created_at, run_date FROM pipeline_health WHERE run_date = ?",
                (yesterday,)
            ).fetchone()
        else:
            row_yday = None

    finally:
        conn.close()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Case 1: Pipeline has not run today ─────────────────────────────────────
    if not row:
        last_run = "never"
        if row_yday:
            last_run = f"{row_yday[3]} (status: {row_yday[0]})"

        subject = f"⚠️ Pipeline DID NOT RUN today ({today})"
        body = (
            f"Watchdog check at {now_str}\n\n"
            f"The daily pipeline has NOT produced a health record for {today}.\n"
            f"Last successful run: {last_run}\n\n"
            "Possible causes:\n"
            "  • Windows Task Scheduler job failed or was skipped\n"
            "  • Pipeline crashed before Phase 5 completed\n"
            "  • Machine was off or asleep during scheduled run time\n\n"
            "Action: Run manually with:\n"
            "  $env:PYTHONUTF8='1'; .\\venv\\Scripts\\python.exe -u -m tools.daily_pipeline"
        )
        print(f"[watchdog] ALERT: Pipeline did not run today ({today}). Last run: {last_run}")
        _send_alert(subject, body)
        return 1

    # ── Case 2: Pipeline ran but health status is fail ─────────────────────────
    status = row[0]
    created_at = row[2]

    if status == "fail":
        import json
        try:
            summary = json.loads(row[1]) if row[1] else {}
            issues = summary.get("issues", [])
        except Exception:
            issues = []

        fail_issues = [i for i in issues if i.get("status") == "fail"]
        warn_issues = [i for i in issues if i.get("status") == "warn"]

        subject = f"🚨 Pipeline Health FAIL — {today} ({len(fail_issues)} failures)"
        lines = [
            f"Watchdog check at {now_str}",
            f"Pipeline ran at {created_at} — status: FAIL",
            f"{len(fail_issues)} FAIL / {len(warn_issues)} WARN",
            "",
            "── FAILURES ──",
        ]
        for i in fail_issues:
            lines.append(f"  ✗ [{i.get('category','?').upper()}] {i.get('check','?')}: {i.get('detail','')}")
        if warn_issues:
            lines += ["", "── WARNINGS ──"]
            for i in warn_issues:
                lines.append(f"  ⚠ [{i.get('category','?').upper()}] {i.get('check','?')}: {i.get('detail','')}")

        body = "\n".join(lines)
        print(f"[watchdog] ALERT: Pipeline ran but health=FAIL ({len(fail_issues)} failures)")
        _send_alert(subject, body)
        return 2

    # ── Case 3: All good ───────────────────────────────────────────────────────
    print(f"[watchdog] OK — pipeline ran {today}, status={status}, completed at {created_at}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
