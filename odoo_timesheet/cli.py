"""Command line interface."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import textwrap

from . import __version__, cron, jobs, notify as notifier
from .config import ConfigError, load_settings
from .filler import fill
from .odoo_client import OdooClient, OdooError
from .planner import build_plan, load_plan, save_plan, validate
from .profile import read_focus, write_focus


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def cmd_status(s, a) -> int:
    odoo = OdooClient.from_settings(s)
    start, end = jobs.period(s, a.start, a.end)
    st = jobs.status(s, odoo, start, end)
    me = odoo.me()
    print(f"Odoo {odoo.base} · db {odoo.db} · user {me['name']} · project '{s.project}'")
    print(f"Period {start} → {end}" + (f" · attendance sheet covers {st['attendance_days']} days" if st["attendance_days"] else ""))
    if st["missing"]:
        print(f"\nMissing days ({len(st['missing'])}, {sum(d.hours - h for d, h in st['missing']):g} h):")
        for d, have in st["missing"]:
            extra = f" ({have:g} h already logged)" if have else ""
            print(f"  {d.weekday} {d.date}  {d.hours - have:>4g} h  {d.reason}{extra}")
    else:
        print("\nNo missing days.")
    if st["flagged"]:
        print(f"\nExisting lines to fix ({len(st['flagged'])}):")
        for f in st["flagged"]:
            print(f"  {f['date']}  line {f['id']}  {f['hours']:g} h  {', '.join(f['problems'])}")
    return 1 if st["missing"] else 0


def _print_plan(plan, s) -> None:
    if not plan.days:
        print("Nothing missing; no plan needed.")
        return
    print(f"Plan {plan.id} · {plan.start} → {plan.end} · descriptions by {plan.summarizer}")
    for n in plan.notes:
        print(f"  note: {n}")
    for d in plan.days:
        flag = "  <-- needs your input" if d.status == "needs_review" else ""
        print(f"\n{d.weekday} {d.date}  {d.hours:g} h  ({len(d.commits)} commits){flag}")
        for l in d.lines:
            print(f"  [{l.task}] {l.hours:g} h")
            print(textwrap.indent(textwrap.fill(l.description, 100), "      "))


def cmd_suggest(s, a) -> int:
    odoo = OdooClient.from_settings(s)
    start, end = jobs.period(s, a.start, a.end)
    plan = build_plan(s, odoo, start, end, summarizer=a.summarizer)
    _print_plan(plan, s)
    if plan.days:
        path = save_plan(plan, s)
        print(f"\nSaved {path}\nReview and fill: python3 timesheet.py open   (or: python3 timesheet.py fill)")
    return 0


def cmd_fill(s, a) -> int:
    plan = load_plan(s, a.plan)
    if not plan:
        print("no plan found; run `suggest` first", file=sys.stderr)
        return 2
    if not plan.pending:
        print("plan has nothing left to fill")
        return 0
    errors = validate(plan)
    if errors:
        print("plan is not valid:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    odoo = OdooClient.from_settings(s)
    dates = set(a.day) if a.day else None
    print("DRY RUN (nothing written)" if a.dry_run else f"Filling Odoo from plan {plan.id}:")
    results = fill(plan, s, odoo, dates=dates, dry_run=a.dry_run)
    ok = [r for r in results if r["ok"]]
    print(f"{'would create' if a.dry_run else 'created'} lines for {len(ok)} day(s); skipped {len(results) - len(ok)}")
    return 0


def cmd_serve(s, a) -> int:
    from .web import serve
    if a.port:
        s.web_port = a.port
    serve(s)
    return 0


def cmd_open(s, a) -> int:
    jobs.ensure_server(s)
    notifier.open_browser(s.web_url)
    print(s.web_url)
    return 0


def cmd_focus(s, a) -> int:
    if a.text:
        write_focus(s.profile_file, " ".join(a.text))
        print(f"saved to {s.profile_file}. Rebuild suggestions to use it (suggest / Rebuild on the page).")
    else:
        focus = read_focus(s.profile_file)
        print(focus or f"no current focus set; edit {s.profile_file} or run: timesheet.py focus \"...\"")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="timesheet.py", description="Suggest and fill Odoo timesheets from git history.")
    ap.add_argument("--config", help="path to config.toml (default: ./config.toml or the project folder)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="first-run setup: asks for Odoo credentials and details, writes .env/config/profile")
    p.add_argument("--dir", help="folder to set up (default: the project folder)")
    p.add_argument("--force", action="store_true", help="overwrite an existing setup")

    p = sub.add_parser("status", help="show missing days and lines to fix")
    p.add_argument("--from", dest="start", type=_date); p.add_argument("--to", dest="end", type=_date)
    p = sub.add_parser("suggest", help="build a plan (suggested lines per missing day) and save it")
    p.add_argument("--from", dest="start", type=_date); p.add_argument("--to", dest="end", type=_date)
    p.add_argument("--summarizer", choices=["auto", "claude", "simple"])
    p = sub.add_parser("fill", help="create the planned lines in Odoo")
    p.add_argument("--plan", default="latest", help="plan id or path (default: latest)")
    p.add_argument("--day", action="append", help="only this date (repeatable)")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("weekly", help="what the cron job runs: check, suggest, start the review page, notify")
    p = sub.add_parser("serve", help="run the local review page"); p.add_argument("--port", type=int)
    sub.add_parser("open", help="open the review page in the browser (starts the server if needed)")
    p = sub.add_parser("focus", help="show or set what you are working on these days (profile.md)")
    p.add_argument("text", nargs="*")
    sub.add_parser("install-cron", help="add the weekly reminder to your crontab")
    sub.add_parser("uninstall-cron", help="remove the reminder from your crontab")
    sub.add_parser("show-cron", help="print the installed reminder line")
    p = sub.add_parser("_notify"); p.add_argument("title"); p.add_argument("body"); p.add_argument("url")

    a = ap.parse_args(argv)
    if a.cmd == "init":
        from pathlib import Path
        from .config import PROJECT_DIR
        from .setup_wizard import run_init
        try:
            return run_init(Path(a.dir) if a.dir else PROJECT_DIR, a.force)
        except KeyboardInterrupt:
            print("\naborted")
            return 130
    if a.cmd == "_notify":
        notifier.notify_and_wait(a.title, a.body, a.url)
        return 0
    try:
        s = load_settings(a.config)
        if a.cmd == "status":
            return cmd_status(s, a)
        if a.cmd == "suggest":
            return cmd_suggest(s, a)
        if a.cmd == "fill":
            return cmd_fill(s, a)
        if a.cmd == "weekly":
            return jobs.run_weekly(s)
        if a.cmd == "serve":
            return cmd_serve(s, a)
        if a.cmd == "open":
            return cmd_open(s, a)
        if a.cmd == "focus":
            return cmd_focus(s, a)
        if a.cmd == "install-cron":
            print("installed:", cron.install(s)); return 0
        if a.cmd == "uninstall-cron":
            print(f"removed {cron.uninstall()} line(s)"); return 0
        if a.cmd == "show-cron":
            lines = cron.show(); print("\n".join(lines) if lines else "no reminder installed"); return 0
    except (ConfigError, OdooError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0
