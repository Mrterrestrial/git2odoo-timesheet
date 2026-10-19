"""Expected working hours per day.

Priority for a given day:
  1. config skip_days            -> 0 h
  2. attendance export (if any)  -> hours from the sheet
  3. weekend                     -> 0 h
  4. public holiday (built-in country list, config extra_holidays, Odoo global leaves) -> 0 h
  5. Odoo time off (hr.leave)    -> 0 h, or reduced for half days / hours
  6. otherwise                   -> hours_per_day
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .attendance import read_attendance


def easter_sunday(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def italian_holidays(year: int) -> dict[str, str]:
    fixed = {
        (1, 1): "Capodanno", (1, 6): "Epifania", (4, 25): "Festa della Liberazione", (5, 1): "Festa del Lavoro",
        (6, 2): "Festa della Repubblica", (8, 15): "Ferragosto", (11, 1): "Ognissanti",
        (12, 8): "Immacolata", (12, 25): "Natale", (12, 26): "Santo Stefano",
    }
    out = {dt.date(year, m, d).isoformat(): n for (m, d), n in fixed.items()}
    out[(easter_sunday(year) + dt.timedelta(days=1)).isoformat()] = "Lunedì dell'Angelo"
    return out


HOLIDAY_PROVIDERS = {"IT": italian_holidays}


@dataclass
class DayHours:
    date: str
    hours: float
    reason: str

    @property
    def weekday(self) -> str:
        return dt.date.fromisoformat(self.date).strftime("%a")


class WorkCalendar:
    def __init__(self, settings, odoo=None):
        self.s = settings
        self.odoo = odoo
        self.attendance: dict[str, float] = {}
        if settings.attendance_file and settings.attendance_file.exists():
            # The export pre-fills future days with the standard hours, so only trust it up to the day it was exported.
            exported = dt.date.fromtimestamp(settings.attendance_file.stat().st_mtime).isoformat()
            self.attendance = {d: h for d, h in read_attendance(settings.attendance_file, settings.attendance_row).items() if d <= exported}

    def _holidays(self, start: dt.date, end: dt.date) -> dict[str, str]:
        out: dict[str, str] = {}
        provider = HOLIDAY_PROVIDERS.get(self.s.holidays_country)
        if provider:
            for y in range(start.year, end.year + 1):
                out.update(provider(y))
        for d in self.s.extra_holidays:
            out[d] = "holiday (config)"
        if self.odoo:
            for g in self.odoo.global_leaves(start, end):
                a = dt.datetime.fromisoformat(g["date_from"]).date()
                b = dt.datetime.fromisoformat(g["date_to"]).date()
                while a <= b:
                    out[a.isoformat()] = g["name"] or "company holiday (Odoo)"
                    a += dt.timedelta(days=1)
        return out

    def _leaves(self, start: dt.date, end: dt.date) -> dict[str, tuple[float, str]]:
        """{date: (hours off, label)}"""
        out: dict[str, tuple[float, str]] = {}
        if not self.odoo:
            return out
        emp = self.odoo.me()["employee_id"]
        for lv in self.odoo.leaves(emp, start, end):
            a = dt.date.fromisoformat(lv["request_date_from"])
            b = dt.date.fromisoformat(lv["request_date_to"])
            label = f"time off: {lv['holiday_status_id'][1]} ({lv['state']})"
            partial = (lv.get("request_unit_half") or lv.get("request_unit_hours")) and a == b
            hours = self.s.hours_per_day * float(lv["number_of_days"]) if partial else self.s.hours_per_day
            while a <= b:
                out[a.isoformat()] = (hours, label)
                a += dt.timedelta(days=1)
        return out

    def expected(self, start: dt.date, end: dt.date) -> list[DayHours]:
        """Every weekday in the range (plus weekend days the attendance sheet marks as worked)."""
        holidays = self._holidays(start, end)
        leaves = self._leaves(start, end)
        out = []
        d = start
        while d <= end:
            iso = d.isoformat()
            if iso in self.s.skip_days:
                item = DayHours(iso, 0, "skipped by config")
            elif iso in self.attendance:
                item = DayHours(iso, self.attendance[iso], "attendance sheet")
            elif d.weekday() >= 5:
                item = DayHours(iso, 0, "weekend")
            elif iso in holidays:
                item = DayHours(iso, 0, f"holiday: {holidays[iso]}")
            elif iso in leaves:
                off, label = leaves[iso]
                item = DayHours(iso, max(0.0, self.s.hours_per_day - off), label)
            else:
                item = DayHours(iso, self.s.hours_per_day, "working day")
            if d.weekday() < 5 or item.hours > 0:
                out.append(item)
            d += dt.timedelta(days=1)
        return out
