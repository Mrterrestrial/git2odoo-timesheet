"""Create the planned lines in Odoo."""
from __future__ import annotations

import datetime as dt

from .planner import Plan, save_plan, validate, logged_by_day


def fill(plan: Plan, settings, odoo, dates: set[str] | None = None, dry_run: bool = False, log=print) -> list[dict]:
    errors = validate(plan)
    if errors:
        raise ValueError("plan is not valid:\n  " + "\n  ".join(errors))
    employee = odoo.me()["employee_id"]
    logged = logged_by_day(odoo, dt.date.fromisoformat(plan.start), dt.date.fromisoformat(plan.end))
    results = []
    for day in plan.days:
        if not day.include or day.status in ("filled", "skipped") or (dates and day.date not in dates):
            continue
        have = logged.get(day.date, 0.0)
        if have + day.hours > day.expected_hours + 0.01:
            day.status = "error"
            day.note = f"skipped: Odoo already has {have:g} h on this day"
            results.append({"date": day.date, "ok": False, "message": day.note})
            log(f"  {day.date}  SKIP  {day.note}")
            continue
        ids = []
        for line in day.lines:
            vals = {
                "date": day.date, "project_id": plan.project_id, "task_id": plan.tasks[line.task],
                "unit_amount": line.hours, "name": line.description.strip(),
            }
            if employee:
                vals["employee_id"] = employee
            log(f"  {day.date}  {line.hours:>4g}h  [{line.task}]  {line.description[:80]}")
            if not dry_run:
                ids.append(odoo.create_timesheet(vals))
        if not dry_run:
            day.status, day.created_ids, day.note = "filled", ids, f"filled {dt.datetime.now():%Y-%m-%d %H:%M}"
        results.append({"date": day.date, "ok": True, "ids": ids, "hours": day.hours})
    if not dry_run:
        save_plan(plan, settings, make_latest=False)
    return results
