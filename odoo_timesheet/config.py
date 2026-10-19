"""Configuration.

Secrets come from `.env` (ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD).
Everything else comes from `config.toml`. Both live in the project directory
(the folder that contains `timesheet.py`) unless `--config` points elsewhere.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


class ConfigError(Exception):
    pass


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (also tolerates `key: value`). Does not touch os.environ."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        values[key] = val
    return values


@dataclass
class Repo:
    path: Path
    label: str
    default_task: str | None = None


@dataclass
class TaskRule:
    pattern: re.Pattern
    task: str


@dataclass
class Settings:
    base_dir: Path
    config_path: Path
    # odoo
    odoo_url: str
    odoo_db: str | None
    odoo_user: str
    odoo_password: str
    project: str
    default_task: str | None
    # calendar
    hours_per_day: float
    start_date: dt.date
    lookback_days: int
    holidays_country: str
    extra_holidays: set[str]
    skip_days: set[str]
    attendance_file: Path | None
    attendance_row: str
    profile_file: Path
    task_catalogue: Path
    # git
    git_author: str
    repos: list[Repo]
    task_rules: list[TaskRule]
    # summarizer
    summarizer_mode: str
    claude_bin: str
    claude_model: str
    summarizer_timeout: int
    language: str
    max_description_chars: int
    # web
    web_host: str
    web_port: int
    # reminder
    reminder_cron: str
    notify_desktop: bool
    open_browser: bool
    notify_when_complete: bool
    # paths
    plans_dir: Path
    logs_dir: Path

    @property
    def web_url(self) -> str:
        return f"http://{self.web_host}:{self.web_port}/"

    def resolve_claude(self) -> str | None:
        """Absolute path of the claude CLI, or None when it is not installed."""
        cand = os.path.expanduser(self.claude_bin)
        if os.path.isabs(cand) and os.access(cand, os.X_OK):
            return cand
        found = shutil.which(cand)
        if found:
            return found
        for extra in ("~/.local/bin", "/usr/local/bin", "/opt/homebrew/bin"):
            p = Path(extra).expanduser() / cand
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
        return None


def _git_global(key: str) -> str:
    try:
        import subprocess
        out = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return ""


def find_config(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        return p
    env = os.environ.get("TIMESHEET_CONFIG")
    if env:
        return find_config(env)
    for cand in (Path.cwd() / "config.toml", PROJECT_DIR / "config.toml"):
        if cand.exists():
            return cand.resolve()
    raise ConfigError("config.toml not found. Run `python3 timesheet.py init` (or ./start.sh) for the first-run setup.")


def load_settings(config_path: str | None = None) -> Settings:
    cfg_path = find_config(config_path)
    base = cfg_path.parent
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))

    env = load_dotenv(base / ".env")
    def secret(key: str, default: str | None = None) -> str | None:
        return os.environ.get(key) or env.get(key) or default

    odoo = raw.get("odoo", {})
    url = secret("ODOO_URL", odoo.get("url"))
    user = secret("ODOO_USER")
    pwd = secret("ODOO_PASSWORD")
    missing = [k for k, v in (("ODOO_URL", url), ("ODOO_USER", user), ("ODOO_PASSWORD", pwd)) if not v]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)} in {base / '.env'} (see .env.example)")
    if not odoo.get("project"):
        raise ConfigError("[odoo].project is required in config.toml")

    cal = raw.get("calendar", {})
    start_raw = cal.get("start_date")
    if isinstance(start_raw, dt.date):
        start_date = start_raw
    elif start_raw:
        start_date = dt.date.fromisoformat(str(start_raw))
    else:
        start_date = dt.date.today().replace(day=1)
    att = cal.get("attendance_file")
    att_path = (base / att).resolve() if att else None

    git = raw.get("git", {})
    author = git.get("author") or _git_global("user.name") or _git_global("user.email")
    if not author:
        raise ConfigError("[git].author is required (git global user.name is not set either)")
    repos = []
    for r in git.get("repos", []):
        if "path" not in r:
            raise ConfigError("every [[git.repos]] entry needs a path")
        path = (base / os.path.expanduser(r["path"])).resolve()
        repos.append(Repo(path=path, label=r.get("label") or path.name, default_task=r.get("default_task")))

    rules = []
    for rule in raw.get("tasks", {}).get("rules", []):
        try:
            rules.append(TaskRule(re.compile(rule["match"], re.I), rule["task"]))
        except (KeyError, re.error) as e:
            raise ConfigError(f"bad [[tasks.rules]] entry {rule}: {e}")

    summ = raw.get("summarizer", {})
    prof = raw.get("profile", {})
    web = raw.get("web", {})
    rem = raw.get("reminder", {})
    paths = raw.get("paths", {})

    plans_dir = (base / paths.get("plans_dir", "plans")).resolve()
    logs_dir = (base / paths.get("logs_dir", "logs")).resolve()
    plans_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        base_dir=base,
        config_path=cfg_path,
        odoo_url=url,
        odoo_db=secret("ODOO_DB", odoo.get("db")) or None,
        odoo_user=user,
        odoo_password=pwd,
        project=odoo["project"],
        default_task=odoo.get("default_task"),
        hours_per_day=float(cal.get("hours_per_day", 8)),
        start_date=start_date,
        lookback_days=int(cal.get("lookback_days", 45)),
        holidays_country=str(cal.get("holidays", "IT")).upper(),
        extra_holidays={str(d) for d in cal.get("extra_holidays", [])},
        skip_days={str(d) for d in cal.get("skip_days", [])},
        attendance_file=att_path,
        attendance_row=cal.get("attendance_row", "Presenza effettiva"),
        profile_file=(base / prof.get("file", "profile.md")).resolve(),
        task_catalogue=(base / prof.get("task_catalogue", "tasks.md")).resolve(),
        git_author=author,
        repos=repos,
        task_rules=rules,
        summarizer_mode=str(summ.get("mode", "auto")).lower(),
        claude_bin=summ.get("claude_bin", "claude"),
        claude_model=summ.get("model", ""),
        summarizer_timeout=int(summ.get("timeout", 180)),
        language=summ.get("language", "English"),
        max_description_chars=int(summ.get("max_description_chars", 400)),
        web_host=web.get("host", "127.0.0.1"),
        web_port=int(web.get("port", 8765)),
        reminder_cron=rem.get("cron", "0 17 * * 5"),
        notify_desktop=bool(rem.get("desktop_notification", True)),
        open_browser=bool(rem.get("open_browser", False)),
        notify_when_complete=bool(rem.get("notify_when_complete", True)),
        plans_dir=plans_dir,
        logs_dir=logs_dir,
    )
