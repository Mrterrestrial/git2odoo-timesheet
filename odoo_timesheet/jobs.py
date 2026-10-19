"""Higher-level operations shared by the CLI, the cron job and the web page."""
from __future__ import annotations

import datetime as dt
import socket
import subprocess
import sys
import time

from . import notify as notifier
from .config import Settings, PROJECT_DIR
from .odoo_client import OdooClient
from .planner import build_plan, missing_days, save_plan
from .workdays import WorkCalendar


def period(s: Settings, start: dt.date | None = None, end: dt.date | None = None) -> tuple[dt.date, dt.date]:
    end = end or dt.date.today()
    start = start or max(s.start_date, end - dt.timedelta(days=s.lookback_days))
    return start, end


def status(s: Settings, odoo: OdooClient, start: dt.date, end: dt.date) -> dict:
    cal = WorkCalendar(s, odoo)
    missing = missing_days(s, odoo, start, end, cal)
    flagged = []
    for l in odoo.timesheets(start, end):
        problems = []
        if not (l["name"] or "").strip() or l["name"].strip() == "/":
            problems.append("no description")
        if not l["task_id"]:
            problems.append("no task")
        if problems:
            flagged.append({"id": l["id"], "date": l["date"], "hours": l["unit_amount"], "problems": problems})
    return {"start": start, "end": end, "missing": missing, "flagged": flagged,
            "attendance_days": len(cal.attendance)}


def server_running(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def ensure_server(s: Settings, log=print) -> bool:
    if server_running(s.web_host, s.web_port):
        return True
    logfile = open(s.logs_dir / "serve.log", "a")
    subprocess.Popen([sys.executable, str(PROJECT_DIR / "timesheet.py"), "--config", str(s.config_path), "serve"],
                     cwd=s.base_dir, start_new_session=True, stdout=logfile, stderr=subprocess.STDOUT)
    for _ in range(20):
        time.sleep(0.25)
        if server_running(s.web_host, s.web_port):
            log(f"started review server at {s.web_url}")
            return True
    log("warning: review server did not start (see logs/serve.log)")
    return False


def run_weekly(s: Settings, log=print) -> int:
    log(f"--- weekly check {dt.datetime.now():%Y-%m-%d %H:%M} ---")
    odoo = OdooClient.from_settings(s)
    start, end = period(s)
    plan = build_plan(s, odoo, start, end, log=log)
    if not plan.days:
        log(f"all days between {start} and {end} are logged")
        if s.notify_when_complete and s.notify_desktop:
            notifier.notify("Odoo timesheet", f"All days up to {end:%a %d %b} are logged. Nothing to do.")
        return 0
    save_plan(plan, s)
    total = sum(d.hours for d in plan.days)
    log(f"{len(plan.days)} missing day(s), {total:g} h, plan {plan.id} ({plan.summarizer} descriptions)")
    ensure_server(s, log)
    body = (f"{len(plan.days)} day(s) missing between {plan.days[0].date} and {plan.days[-1].date} "
            f"({total:g} h). Suggestions are ready: review and fill with one click.")
    if s.notify_desktop:
        notifier.notify("Odoo timesheet reminder", body, url=s.web_url)
    if s.open_browser:
        notifier.open_browser(s.web_url)
    return 0
