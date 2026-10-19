"""Build, store and load plans: the per-day suggestions the user reviews before filling."""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import gitlog, summarize
from .profile import load_text
from .workdays import WorkCalendar


@dataclass
class Line:
    task: str
    hours: float
    description: str
    repos: list[str] = field(default_factory=list)
    commit_count: int = 0


@dataclass
class Day:
    date: str
    expected_hours: float
    logged_hours: float
    reason: str
    lines: list[Line]
    commits: list[dict]
    status: str = "suggested"      # suggested | needs_review | filled | skipped | error
    include: bool = True
    note: str = ""
    created_ids: list[int] = field(default_factory=list)

    @property
    def hours(self) -> float:
        return round(sum(l.hours for l in self.lines), 2)

    @property
    def weekday(self) -> str:
        return dt.date.fromisoformat(self.date).strftime("%a")


@dataclass
class Plan:
    id: str
    created_at: str
    start: str
    end: str
    project: str
    project_id: int
    tasks: dict[str, int]
    summarizer: str
    days: list[Day]
    notes: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[Day]:
        return [d for d in self.days if d.status not in ("filled", "skipped")]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        days = [Day(**{**x, "lines": [Line(**l) for l in x["lines"]]}) for x in d["days"]]
        return cls(**{**d, "days": days})


# ------------------------------------------------------------------ helpers
def round_half(x: float) -> float:
    return round(x * 2) / 2


def split_hours(total: float, weights: list[int]) -> list[float]:
    if len(weights) == 1:
        return [round(total, 2)]
    s = sum(weights) or len(weights)
    parts = [round_half(total * w / s) for w in weights]
    parts[weights.index(max(weights))] += round(total - sum(parts), 2)
    return [round(p, 2) for p in parts]


def task_for_commit(commit, settings, tasks: dict[str, int], repo_defaults: dict[str, str | None]) -> str:
    # the subject is the reliable signal; the body is only consulted when no rule matches the subject
    for text in (commit.subject, commit.body):
        for rule in settings.task_rules:
            if text and rule.pattern.search(text) and rule.task in tasks:
                return rule.task
    cand = repo_defaults.get(commit.repo) or settings.default_task
    if cand and cand in tasks:
        return cand
    raise ValueError(
        f"no task for commit '{commit.subject}' ({commit.repo}): set [odoo].default_task or the repo default_task "
        f"to one of {sorted(tasks)[:8]}..."
    )


def logged_by_day(odoo, start: dt.date, end: dt.date) -> dict[str, float]:
    out: dict[str, float] = {}
    for l in odoo.timesheets(start, end):
        out[l["date"]] = out.get(l["date"], 0) + float(l["unit_amount"])
    return out


def missing_days(settings, odoo, start: dt.date, end: dt.date, calendar: WorkCalendar | None = None):
    """[(DayHours, logged_hours)] for days whose logged hours fall short of the expected hours."""
    cal = calendar or WorkCalendar(settings, odoo)
    logged = logged_by_day(odoo, start, end)
    out = []
    for d in cal.expected(start, end):
        have = logged.get(d.date, 0.0)
        if d.hours - have >= 0.5:
            out.append((d, have))
    return out


# -------------------------------------------------------------------- build
def build_plan(settings, odoo, start: dt.date, end: dt.date, log=print, summarizer: str | None = None) -> Plan:
    project = odoo.find_project(settings.project)
    tasks = odoo.tasks(project["id"])
    repo_defaults = {r.label: r.default_task for r in settings.repos}
    missing = missing_days(settings, odoo, start, end)
    plan = Plan(
        id=dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
        created_at=dt.datetime.now().isoformat(timespec="seconds"),
        start=start.isoformat(), end=end.isoformat(),
        project=project["name"], project_id=project["id"], tasks=tasks, summarizer="none", days=[],
    )
    if not missing:
        return plan

    commits = gitlog.collect(settings.repos, settings.git_author, start - dt.timedelta(days=10), end, log=log)
    items, context = [], []
    for d, have in missing:
        day_commits = commits.get(d.date, [])
        remaining = round(d.hours - have, 2)
        day = Day(date=d.date, expected_hours=d.hours, logged_hours=have, reason=d.reason, lines=[],
                  commits=[c.to_dict() for c in day_commits])
        if have:
            day.note = f"{have:g} h already logged on this day"
        if day_commits:
            groups: dict[str, list] = {}
            for c in day_commits:
                groups.setdefault(task_for_commit(c, settings, tasks, repo_defaults), []).append(c)
            hours = split_hours(remaining, [len(v) for v in groups.values()])
            for (task, cs), h in zip(groups.items(), hours):
                repos = sorted({c.repo for c in cs})
                line = Line(task=task, hours=h, description="", repos=repos, commit_count=len(cs))
                day.lines.append(line)
                items.append({"key": f"{d.date}#{len(day.lines)}", "date": d.date, "repos": repos, "gap": False,
                              "task_guess": task,
                              "commits": [{"repo": c.repo, "subject": c.subject, "body": c.body[:400]} for c in cs]})
        else:
            task = settings.default_task or next((r.default_task for r in settings.repos if r.default_task), None)
            if not task or task not in tasks:
                task = next(iter(tasks))
            day.lines.append(Line(task=task, hours=remaining, description="", repos=[], commit_count=0))
            day.status = "needs_review"
            items.append({"key": f"{d.date}#1", "date": d.date, "repos": [], "gap": True, "task_guess": task, "commits": []})
        plan.days.append(day)

    for date, cs in sorted(commits.items()):
        if date < start.isoformat() or date > end.isoformat():
            context += [{"date": date, "repo": c.repo, "subject": c.subject} for c in cs]

    # descriptions
    mode = (summarizer or settings.summarizer_mode).lower()
    texts: dict[str, dict] = {}
    if mode in ("auto", "claude") and items:
        try:
            log("asking claude for descriptions...")
            texts = summarize.claude_descriptions(
                items, context[-40:], settings, profile=load_text(settings.profile_file),
                catalogue=load_text(settings.task_catalogue), allowed_tasks=sorted(tasks))
            plan.summarizer = "claude"
        except Exception as e:  # noqa: BLE001 - any failure falls back to the simple engine
            if mode == "claude":
                raise
            log(f"claude summarizer unavailable ({e}); using simple descriptions")
            plan.notes.append(f"claude summarizer unavailable: {e}")
    if not texts:
        plan.summarizer = "simple"
    all_commits = [c for cs in commits.values() for c in cs]
    for day in plan.days:
        for i, line in enumerate(day.lines, 1):
            key = f"{day.date}#{i}"
            if key in texts:
                line.description = texts[key]["description"]
                if texts[key]["task"] in tasks:
                    line.task = texts[key]["task"]
            elif line.commit_count:
                cs = [gitlog.Commit(**c) for c in day.commits]
                line.description = summarize.simple_group_description(
                    line.repos, [c for c in cs if task_for_commit(c, settings, tasks, repo_defaults) == line.task],
                    settings.max_description_chars)
            else:
                later = sorted((c for c in all_commits if c.date > day.date), key=lambda c: c.date)
                earlier = sorted((c for c in all_commits if c.date < day.date), key=lambda c: c.date, reverse=True)
                line.description = summarize.simple_gap_description(later or earlier, settings.max_description_chars)
        _merge_same_task(day)
    return plan


def _merge_same_task(day: Day) -> None:
    """After the engine may have re-assigned tasks, lines on the same task become one line."""
    merged: dict[str, Line] = {}
    for l in day.lines:
        if l.task in merged:
            m = merged[l.task]
            m.hours = round(m.hours + l.hours, 2)
            m.description = (m.description.rstrip(". ") + ". " + l.description).strip()
            m.repos = sorted(set(m.repos) | set(l.repos))
            m.commit_count += l.commit_count
        else:
            merged[l.task] = l
    day.lines = list(merged.values())


# ------------------------------------------------------------------ storage
def plan_path(settings, plan_id: str) -> Path:
    return settings.plans_dir / f"{plan_id}.json"


def save_plan(plan: Plan, settings, make_latest: bool = True) -> Path:
    path = plan_path(settings, plan.id)
    path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if make_latest:
        (settings.plans_dir / "latest").write_text(plan.id, encoding="utf-8")
    return path


def load_plan(settings, ref: str = "latest") -> Plan | None:
    if ref == "latest":
        marker = settings.plans_dir / "latest"
        if not marker.exists():
            return None
        ref = marker.read_text(encoding="utf-8").strip()
    path = Path(ref) if ref.endswith(".json") else plan_path(settings, ref)
    if not path.exists():
        return None
    return Plan.from_dict(json.loads(path.read_text(encoding="utf-8")))


def validate(plan: Plan) -> list[str]:
    errors = []
    for day in plan.days:
        if not day.include or day.status in ("filled", "skipped"):
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day.date):
            errors.append(f"{day.date}: bad date")
        if not day.lines:
            errors.append(f"{day.date}: no lines")
        for l in day.lines:
            if l.task not in plan.tasks:
                errors.append(f"{day.date}: unknown task '{l.task}'")
            if not (0 < l.hours <= 24):
                errors.append(f"{day.date}: bad hours {l.hours}")
            if not l.description.strip() or l.description.strip() == "/":
                errors.append(f"{day.date}: empty description")
        if day.hours > day.expected_hours - day.logged_hours + 0.01:
            errors.append(f"{day.date}: {day.hours:g} h planned but only {day.expected_hours - day.logged_hours:g} h left to log")
    return errors
