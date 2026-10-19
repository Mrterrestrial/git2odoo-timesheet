"""First-run wizard: asks for Odoo credentials and project details, verifies them against Odoo,
and writes .env, config.toml, profile.md and tasks.md. Run with `python3 timesheet.py init`."""
from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .odoo_client import OdooClient, OdooError
from .profile import FOCUS_HEADING


def _toml(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def ask(prompt: str, default: str | None = None, secret: bool = False, required: bool = True) -> str:
    label = f"{prompt} [{default}]: " if default not in (None, "") else f"{prompt}: "
    while True:
        try:
            if secret and sys.stdin.isatty():
                val = getpass.getpass(label)
            else:  # piped answers (tests, automation): plain read, no /dev/tty
                val = input(label)
        except EOFError:
            print("\naborted: no more input")
            raise SystemExit(130)
        val = val.strip()
        if not val and default is not None:
            return default
        if val or not required:
            return val
        print("  a value is required")


def yes(prompt: str, default: bool = True) -> bool:
    return ask(f"{prompt} (y/n)", "y" if default else "n").lower().startswith("y")


def choose(prompt: str, options: list[str], default: str | None = None) -> str:
    """Pick one option by number or by (part of) its name."""
    for i, o in enumerate(options, 1):
        print(f"  {i:3d}. {o}")
    while True:
        val = ask(prompt, default)
        if val.isdigit() and 1 <= int(val) <= len(options):
            return options[int(val) - 1]
        hits = [o for o in options if val.lower() == o.lower()] or [o for o in options if val.lower() in o.lower()]
        if len(hits) == 1:
            return hits[0]
        print(f"  {'no match' if not hits else 'ambiguous: ' + ', '.join(hits[:6])}; type a number or a unique name")


def _git_global(key: str) -> str:
    try:
        return subprocess.run(["git", "config", "--global", key], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def connect() -> tuple[OdooClient, dict]:
    """Ask for the Odoo credentials until a connection works."""
    print("\n== Odoo ==")
    defaults = {"url": "", "db": "", "user": _git_global("user.email").split("@")[0]}
    while True:
        url = ask("Odoo URL (e.g. https://odoo.example.com, the dashboard link is fine)", defaults["url"] or None)
        defaults["url"] = url
        db = ask("Database name (leave empty to detect)", defaults["db"], required=False)
        if not db:
            try:
                dbs = OdooClient.list_databases(url)
            except OdooError as e:
                print(f"  {e}; please type the database name")
                continue
            if not dbs:
                print("  the server lists no databases; please type the name")
                continue
            db = dbs[0] if len(dbs) == 1 else choose("Which database", dbs)
            print(f"  using database '{db}'")
        defaults["db"] = db
        user = ask("Login", defaults["user"] or None)
        password = ask("Password", secret=True)
        defaults["user"] = user
        try:
            odoo = OdooClient(url, user, password, db or None)
            me = odoo.me()
            print(f"  connected: {me['name']} on database '{odoo.db}'" + ("" if me["employee_id"] else "  (warning: no employee linked to this user)"))
            return odoo, {"ODOO_URL": odoo.base, "ODOO_DB": odoo.db, "ODOO_USER": user, "ODOO_PASSWORD": password}
        except OdooError as e:
            print(f"  {e}\n  let's try again")


def pick_project(odoo: OdooClient) -> dict:
    print("\n== Project ==")
    while True:
        name = ask("Project name (or part of it)")
        recs = odoo.call("project.project", "search_read", [["name", "ilike", name], ["allow_timesheets", "=", True]],
                         fields=["name"], order="name")
        if not recs:
            print("  no project matches; try another name")
            continue
        if len(recs) == 1:
            print(f"  using '{recs[0]['name']}'")
            return recs[0]
        chosen = choose("Which one", [r["name"] for r in recs])
        return next(r for r in recs if r["name"] == chosen)


def write_env(base: Path, env: dict) -> None:
    path = base / ".env"
    path.write_text("# Odoo credentials. Never commit this file.\n" + "".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    os.chmod(path, 0o600)


def write_tasks_md(base: Path, project: str, tasks: list[str]) -> bool:
    path = base / "tasks.md"
    if path.exists():
        return False
    body = [f"# Task catalogue — {project}", "",
            "Shared by everyone on the project: what belongs to each Odoo task. Names must match Odoo exactly.",
            "Fill in the one-line meaning under each heading; the description engine reads this file on every run.", ""]
    for t in tasks:
        body += [f"## {t}", "(describe what belongs here)", ""]
    path.write_text("\n".join(body), encoding="utf-8")
    return True


def write_profile_md(base: Path, role: str, resp: str, focus: str) -> None:
    path = base / "profile.md"
    text = (f"# Profile\n\n## Role\n{role or 'Describe your role on the project.'}\n\n## Responsibilities\n"
            + ("".join(f"- {r.strip()}\n" for r in resp.split(";") if r.strip()) or "- What you normally take care of.\n")
            + f"\n{FOCUS_HEADING}\n_Updated {dt.date.today():%Y-%m-%d}_\n\n{focus or 'What you are working on these days.'}\n\n"
            "## Writing preferences\n- English, past tense, one or two sentences per line, name the feature not the file.\n"
            "- Prefix with the repository name when the day touches more than one.\n")
    path.write_text(text, encoding="utf-8")


CONFIG_TEMPLATE = """# Generated by `timesheet.py init` on {date}. Edit freely; secrets live in .env.

[odoo]
project = {project}
default_task = {default_task}

[calendar]
hours_per_day = {hours}
start_date = "{start}"
lookback_days = 45
holidays = "{country}"              # built-in public holidays ("IT") or "" for none
extra_holidays = []                 # company closing days not in Odoo, e.g. ["2026-12-24"]
skip_days = []                      # days never to log
{attendance}

[git]
author = {author}
{repos}
# Optional regex rules: commit text -> task, first match wins. With the claude engine, tasks.md and
# profile.md usually make them unnecessary. Example:
# [[tasks.rules]]
# match = "payment|invoice"
# task = "Billing"

[profile]
file = "profile.md"
task_catalogue = "tasks.md"

[summarizer]
mode = "auto"                       # auto: claude CLI when available, else simple | claude | simple
claude_bin = "claude"
model = ""
timeout = 180
language = "English"
max_description_chars = 400

[web]
host = "127.0.0.1"
port = 8765

[reminder]
cron = "{cron}"
desktop_notification = true
open_browser = false
notify_when_complete = true
"""


def run_init(base: Path, force: bool = False) -> int:
    base = base.resolve()
    existing = [p.name for p in (base / ".env", base / "config.toml") if p.exists()]
    if existing and not force:
        print(f"{', '.join(existing)} already exist in {base}. Run `timesheet.py init --force` to redo the setup.")
        return 2
    print(f"Odoo timesheet filler — first-run setup in {base}\n(press Enter to accept a default shown in [brackets])")

    odoo, env = connect()
    project = pick_project(odoo)
    task_names = sorted(odoo.tasks(project["id"]))
    if not task_names:
        print("  this project has no tasks; create at least one in Odoo first")
        return 2
    print("\nTasks of the project:")
    default_task = choose("Default task when nothing else matches", task_names, task_names[0])

    print("\n== Git repositories ==")
    author = ask("Author pattern for `git log --author`", _git_global("user.name") or env["ODOO_USER"])
    repos = []
    while True:
        raw = ask("Path to a repository (empty to finish)" if repos else "Path to a repository", required=not repos)
        if not raw:
            break
        path = Path(os.path.expanduser(raw))
        path = path if path.is_absolute() else (base / path)
        if not (path / ".git").exists():
            print(f"  {path} is not a git repository (continuing anyway, it will be skipped until it exists)")
        label = ask("  label used in descriptions", path.name)
        print("  default task for this repository:")
        rtask = choose("  task", task_names, default_task)
        repos.append((raw, label, rtask))

    print("\n== Calendar ==")
    hours = ask("Hours per working day", "8")
    start = ask("First day to consider (YYYY-MM-DD)", dt.date.today().replace(day=1).isoformat())
    country = ask("Public holidays country code (IT, or empty for none)", "IT", required=False)
    xlsx = sorted(p.name for p in base.glob("*.xlsx"))
    attendance = ""
    if xlsx and yes(f"Use the attendance export {xlsx[0]} for hours and days off", True):
        attendance = f'attendance_file = "{xlsx[0]}"\nattendance_row = "Presenza effettiva"'
    else:
        attendance = '# attendance_file = "Foglio_presenze.xlsx"   # optional HR export overriding hours per day'

    print("\n== You ==")
    role = ask("Your role on the project (e.g. backend developer, UI designer, devops)", required=False)
    resp = ask("Main responsibilities, separated by ';'", required=False)
    focus = ask("What are you working on these days", required=False)

    print("\n== Reminder ==")
    cron = ask("Cron schedule for the weekly reminder", "0 17 * * 5")

    repos_toml = "".join(
        f'\n[[git.repos]]\npath = {_toml(p)}\nlabel = {_toml(l)}\ndefault_task = {_toml(t)}\n' for p, l, t in repos)
    config = CONFIG_TEMPLATE.format(
        date=dt.date.today().isoformat(), project=_toml(project["name"]), default_task=_toml(default_task),
        hours=hours, start=start, country=country.upper(), attendance=attendance, author=_toml(author),
        repos=repos_toml, cron=cron)
    write_env(base, env)
    (base / "config.toml").write_text(config, encoding="utf-8")
    write_profile_md(base, role, resp, focus)
    made_tasks = write_tasks_md(base, project["name"], task_names)
    for d in ("plans", "logs"):
        (base / d).mkdir(exist_ok=True)

    print(f"\nWritten: .env (chmod 600), config.toml, profile.md" + (", tasks.md (skeleton, please describe each task)" if made_tasks else ""))
    claude = shutil.which("claude") or shutil.which(os.path.expanduser("~/.local/bin/claude"))
    print("Description engine: " + ("claude CLI found" if claude else "claude CLI not found, plain commit summaries will be used"))
    if shutil.which("crontab") and yes("Install the weekly reminder in your crontab now", True):
        from . import cron as cronmod
        from .config import load_settings
        print("  " + cronmod.install(load_settings(str(base / "config.toml"))))
    print("\nNext: python3 timesheet.py status   |   python3 timesheet.py open")
    return 0
