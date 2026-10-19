"""Personal profile (who you are, what you are working on) and the shared task catalogue.

Both are plain markdown files. Their text is given to the description engine so that the
suggested task and wording fit the person: a designer, a backend developer and a devops
engineer on the same project share the Odoo task list but map their work differently.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

FOCUS_HEADING = "## Current focus"

PROFILE_TEMPLATE = """# Profile

## Role
Describe your role on the project in one or two lines (e.g. backend developer, UI designer, devops).

## Responsibilities
- What you normally take care of.

{focus_heading}
Write here what you are working on these days. Update it any time
(`python3 timesheet.py focus "..."` or the box on the review page).

## Writing preferences
- Language, tone, things to always or never mention.
"""


def load_text(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _ensure(path: Path) -> str:
    if not path.exists():
        path.write_text(PROFILE_TEMPLATE.format(focus_heading=FOCUS_HEADING), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    m = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return m.end(), end


def read_focus(path: Path) -> str:
    if not path.exists():
        return ""
    span = _section_span(path.read_text(encoding="utf-8"), FOCUS_HEADING)
    if not span:
        return ""
    text = path.read_text(encoding="utf-8")[span[0]:span[1]].strip()
    return text


def write_focus(path: Path, focus: str) -> None:
    """Replace the 'Current focus' section (created if missing) and stamp the date."""
    text = _ensure(path)
    body = f"\n_Updated {dt.date.today():%Y-%m-%d}_\n\n{focus.strip()}\n\n"
    span = _section_span(text, FOCUS_HEADING)
    if span:
        text = text[:span[0]] + body + text[span[1]:]
    else:
        text = text.rstrip() + f"\n\n{FOCUS_HEADING}\n{body}"
    path.write_text(text, encoding="utf-8")
