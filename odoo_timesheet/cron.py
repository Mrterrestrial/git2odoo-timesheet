"""Install / remove the weekly reminder line in the user's crontab."""
from __future__ import annotations

import subprocess
import sys

from .config import Settings

MARK = "# odoo-timesheet-reminder"


def _read() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        if "no crontab" in (proc.stderr + proc.stdout).lower():
            return []
        raise RuntimeError(f"crontab -l failed: {proc.stderr.strip()}")
    return proc.stdout.splitlines()


def _write(lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n"
    proc = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"crontab install failed: {proc.stderr.strip()}")


def command_line(s: Settings) -> str:
    log = s.logs_dir / "weekly.log"
    return f'{s.reminder_cron} cd "{s.base_dir}" && "{sys.executable}" timesheet.py weekly >> "{log}" 2>&1 {MARK}'


def install(s: Settings) -> str:
    line = command_line(s)
    _write([l for l in _read() if MARK not in l] + [line])
    return line


def uninstall() -> int:
    lines = _read()
    kept = [l for l in lines if MARK not in l]
    if len(kept) != len(lines):
        _write(kept)
    return len(lines) - len(kept)


def show() -> list[str]:
    return [l for l in _read() if MARK in l]
