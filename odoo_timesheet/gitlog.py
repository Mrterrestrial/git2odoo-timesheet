"""Collect the user's commits per day from the configured repositories."""
from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import Repo

RS, FS = "\x1e", "\x1f"


@dataclass
class Commit:
    repo: str
    sha: str
    date: str
    subject: str
    body: str

    def to_dict(self) -> dict:
        return asdict(self)


def _git_log(repo: Repo, author: str, start: dt.date, end: dt.date) -> list[Commit]:
    if not (repo.path / ".git").exists() and not (repo.path / "HEAD").exists():
        raise FileNotFoundError(f"{repo.path} is not a git repository")
    cmd = [
        "git", "-C", str(repo.path), "log", "--all", "--no-merges", f"--author={author}",
        f"--since={(start - dt.timedelta(days=2)).isoformat()}T00:00:00",
        f"--until={(end + dt.timedelta(days=2)).isoformat()}T23:59:59",
        "--date=format-local:%Y-%m-%d", f"--format={RS}%H{FS}%ad{FS}%s{FS}%b",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
    commits = []
    for rec in out.split(RS):
        if not rec.strip():
            continue
        sha, date, subject, body = (rec.split(FS, 3) + ["", "", ""])[:4]
        if start.isoformat() <= date <= end.isoformat():
            commits.append(Commit(repo.label, sha[:10], date, subject.strip(), body.strip()))
    return commits


def collect(repos: list[Repo], author: str, start: dt.date, end: dt.date, log=None) -> dict[str, list[Commit]]:
    """{ISO date: [commits]} for all repos, newest repo order preserved, duplicates (same subject, same day) dropped."""
    by_day: dict[str, list[Commit]] = {}
    seen: set[tuple[str, str, str]] = set()
    for repo in repos:
        try:
            commits = _git_log(repo, author, start, end)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            if log:
                log(f"warning: skipping repo {repo.label}: {e}")
            continue
        for c in commits:
            key = (c.repo, c.date, c.subject)
            if key in seen:
                continue
            seen.add(key)
            by_day.setdefault(c.date, []).append(c)
    return by_day
