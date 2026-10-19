"""Desktop notification and browser opening (Linux, libnotify / xdg-open)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .config import PROJECT_DIR

ENTRY = PROJECT_DIR / "timesheet.py"
APP = "Odoo timesheet"


def gui_env() -> dict:
    """Environment that lets a cron job talk to the user's desktop session."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def open_browser(url: str) -> bool:
    opener = shutil.which("xdg-open")
    if not opener:
        return False
    subprocess.Popen([opener, url], env=gui_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def notify(title: str, body: str, url: str | None = None) -> bool:
    """Show a notification. With `url`, an 'Open' button opens it; the wait happens in a detached helper."""
    if not shutil.which("notify-send"):
        return False
    if url:
        subprocess.Popen([sys.executable, str(ENTRY), "_notify", title, body, url], env=gui_env(),
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    subprocess.run(["notify-send", "-a", APP, "-i", "x-office-calendar", title, body], env=gui_env(), timeout=15)
    return True


def notify_and_wait(title: str, body: str, url: str, timeout: int = 4 * 3600) -> None:
    """Blocking variant used by the helper process: waits for the click, then opens the browser."""
    cmd = ["notify-send", "-a", APP, "-i", "x-office-calendar", "-A", "open=Open review page", title, body]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=gui_env(), timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return
    if out.strip() == "open":
        open_browser(url)
