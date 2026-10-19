"""Thin XML-RPC client for the Odoo models the filler needs."""
from __future__ import annotations

import datetime as dt
import urllib.parse
import xmlrpc.client


class OdooError(Exception):
    pass


class OdooClient:
    def __init__(self, url: str, user: str, password: str, db: str | None = None):
        parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
        self.base = f"{parts.scheme}://{parts.netloc}"
        self.user, self._password = user, password
        self.db = db or self._detect_db()
        common = xmlrpc.client.ServerProxy(self.base + "/xmlrpc/2/common")
        try:
            self.uid = common.authenticate(self.db, user, password, {})
        except Exception as e:  # network, bad host...
            raise OdooError(f"cannot reach Odoo at {self.base}: {e}") from e
        if not self.uid:
            raise OdooError(f"Odoo authentication failed for user '{user}' on database '{self.db}'")
        self._models = xmlrpc.client.ServerProxy(self.base + "/xmlrpc/2/object", allow_none=True)
        self._me: dict | None = None

    @classmethod
    def from_settings(cls, s) -> "OdooClient":
        return cls(s.odoo_url, s.odoo_user, s.odoo_password, s.odoo_db)

    # ---------------------------------------------------------------- basics
    @staticmethod
    def list_databases(url: str) -> list[str]:
        parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
        try:
            return list(xmlrpc.client.ServerProxy(f"{parts.scheme}://{parts.netloc}/xmlrpc/2/db").list())
        except Exception as e:
            raise OdooError(f"cannot list databases on {parts.netloc}: {e}") from e

    def _detect_db(self) -> str:
        try:
            dbs = xmlrpc.client.ServerProxy(self.base + "/xmlrpc/2/db").list()
        except Exception as e:
            raise OdooError(f"cannot list databases on {self.base}; set ODOO_DB in .env ({e})") from e
        if len(dbs) != 1:
            raise OdooError(f"server has {len(dbs)} databases {dbs}; set ODOO_DB in .env")
        return dbs[0]

    def call(self, model: str, method: str, *args, **kwargs):
        try:
            return self._models.execute_kw(self.db, self.uid, self._password, model, method, list(args), kwargs)
        except xmlrpc.client.Fault as e:
            raise OdooError(f"{model}.{method}: {e.faultString.strip().splitlines()[-1]}") from e

    def me(self) -> dict:
        if self._me is None:
            rec = self.call("res.users", "read", [self.uid], fields=["name", "login", "employee_id", "tz"])[0]
            self._me = {
                "uid": self.uid,
                "name": rec["name"],
                "login": rec["login"],
                "employee_id": rec["employee_id"][0] if rec["employee_id"] else None,
                "tz": rec.get("tz") or "UTC",
            }
        return self._me

    # --------------------------------------------------------------- project
    def find_project(self, name: str) -> dict:
        recs = self.call("project.project", "search_read", [["name", "ilike", name]], fields=["name", "allow_timesheets"])
        exact = [r for r in recs if r["name"].strip().lower() == name.strip().lower()]
        recs = exact or recs
        if not recs:
            raise OdooError(f"project '{name}' not found (or not visible to {self.user})")
        if len(recs) > 1:
            raise OdooError(f"project name '{name}' is ambiguous: {[r['name'] for r in recs]}")
        if not recs[0].get("allow_timesheets", True):
            raise OdooError(f"project '{recs[0]['name']}' does not allow timesheets")
        return recs[0]

    def tasks(self, project_id: int) -> dict[str, int]:
        recs = self.call("project.task", "search_read", [["project_id", "=", project_id]],
                         fields=["name"], context={"active_test": False})
        return {r["name"]: r["id"] for r in recs}

    # ------------------------------------------------------------ timesheets
    def timesheets(self, start: dt.date, end: dt.date) -> list[dict]:
        return self.call(
            "account.analytic.line", "search_read",
            [["user_id", "=", self.uid], ["project_id", "!=", False],
             ["date", ">=", start.isoformat()], ["date", "<=", end.isoformat()]],
            fields=["id", "date", "unit_amount", "name", "task_id", "project_id"], order="date,id")

    def create_timesheet(self, vals: dict) -> int:
        return self.call("account.analytic.line", "create", vals)

    # -------------------------------------------------------------- time off
    def leaves(self, employee_id: int | None, start: dt.date, end: dt.date) -> list[dict]:
        """Validated or pending hr.leave records overlapping the range. Empty if the module is absent."""
        if not employee_id:
            return []
        try:
            return self.call(
                "hr.leave", "search_read",
                [["employee_id", "=", employee_id], ["state", "in", ["confirm", "validate1", "validate"]],
                 ["request_date_to", ">=", start.isoformat()], ["request_date_from", "<=", end.isoformat()]],
                fields=["request_date_from", "request_date_to", "number_of_days", "holiday_status_id", "state",
                        "request_unit_half", "request_unit_hours"])
        except OdooError:
            return []

    def global_leaves(self, start: dt.date, end: dt.date) -> list[dict]:
        """Company-wide closing days (public holidays configured in Odoo)."""
        try:
            return self.call(
                "resource.calendar.leaves", "search_read",
                [["resource_id", "=", False], ["date_to", ">=", start.isoformat()], ["date_from", "<=", end.isoformat() + " 23:59:59"]],
                fields=["name", "date_from", "date_to"])
        except OdooError:
            return []
