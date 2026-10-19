"""Offline unit tests: python3 -m unittest discover -s tests -v"""
import datetime as dt
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from odoo_timesheet import summarize, planner, config  # noqa: E402
from odoo_timesheet.gitlog import Commit  # noqa: E402
from odoo_timesheet.workdays import easter_sunday, italian_holidays, WorkCalendar  # noqa: E402
from odoo_timesheet.attendance import read_attendance  # noqa: E402


def fake_settings(**over):
    base = dict(hours_per_day=8.0, skip_days=set(), extra_holidays=set(), holidays_country="IT",
                attendance_file=None, attendance_row="Presenza effettiva", default_task="Dev",
                task_rules=[], repos=[], max_description_chars=400)
    base.update(over)
    return SimpleNamespace(**base)


class SummarizeTests(unittest.TestCase):
    def test_clean_subject(self):
        self.assertEqual(summarize.clean_subject("[FIX] Dossier PDF prints notes"), "fixed dossier PDF prints notes")
        self.assertEqual(summarize.clean_subject("feat(api): Add stats endpoint."), "added add stats endpoint")
        self.assertEqual(summarize.clean_subject("plain subject"), "Plain subject")

    def test_group_description_limit(self):
        cs = [Commit("Backend", "a", "2026-10-12", f"[ADD] Feature number {i} with a long name", "") for i in range(30)]
        text = summarize.simple_group_description(["Backend"], cs, 120)
        self.assertTrue(text.startswith("Backend: added feature number 0"))
        self.assertLessEqual(len(text), 120)

    def test_gap_description(self):
        cs = [Commit("Backend", "a", "2026-10-14", "[CHANGE] Offer PDF shows the title", "")]
        self.assertEqual(summarize.simple_gap_description(cs, 200), "Work in progress on: offer PDF shows the title.")

    def test_extract_json(self):
        self.assertEqual(summarize._extract_json('```json\n{"a": "b"}\n```'), {"a": "b"})


class WorkdayTests(unittest.TestCase):
    def test_easter(self):
        self.assertEqual(easter_sunday(2026), dt.date(2026, 4, 5))
        self.assertEqual(easter_sunday(2025), dt.date(2025, 4, 20))

    def test_holidays(self):
        h = italian_holidays(2026)
        self.assertIn("2026-04-06", h)
        self.assertIn("2026-06-02", h)

    def test_calendar(self):
        s = fake_settings(skip_days={"2026-10-14"})
        days = {d.date: d for d in WorkCalendar(s).expected(dt.date(2026, 10, 10), dt.date(2026, 10, 19))}
        self.assertNotIn("2026-10-10", days)              # Saturday
        self.assertEqual(days["2026-10-12"].hours, 8)
        self.assertEqual(days["2026-10-14"].hours, 0)      # skipped
        self.assertEqual(WorkCalendar(s).expected(dt.date(2026, 6, 2), dt.date(2026, 6, 2))[0].hours, 0)  # holiday


class PlannerTests(unittest.TestCase):
    def test_split_hours(self):
        self.assertEqual(planner.split_hours(8, [3]), [8])
        self.assertEqual(planner.split_hours(8, [3, 1]), [6, 2])
        self.assertEqual(sum(planner.split_hours(7.5, [1, 1, 1])), 7.5)

    def test_task_rules(self):
        s = fake_settings(task_rules=[config.TaskRule(re.compile("payment", re.I), "Billing")])
        tasks = {"Billing": 1, "Dev": 2, "Front": 3}
        c = Commit("Backend", "a", "2026-10-12", "[FIX] Payment retry", "")
        self.assertEqual(planner.task_for_commit(c, s, tasks, {"Backend": "Dev"}), "Billing")
        c2 = Commit("Frontend", "b", "2026-10-12", "[ADD] Button", "")
        self.assertEqual(planner.task_for_commit(c2, s, tasks, {"Frontend": "Front"}), "Front")
        self.assertEqual(planner.task_for_commit(c2, s, tasks, {}), "Dev")
        with self.assertRaises(ValueError):
            planner.task_for_commit(c2, s, {"Other": 9}, {})

    def test_plan_roundtrip_and_validate(self):
        day = planner.Day("2026-10-12", 8, 0, "working day", [planner.Line("Dev", 8, "did things")], [])
        plan = planner.Plan("x", "now", "2026-10-12", "2026-10-12", "P", 1, {"Dev": 2}, "simple", [day])
        again = planner.Plan.from_dict(json.loads(json.dumps(plan.to_dict())))
        self.assertEqual(again.days[0].lines[0].task, "Dev")
        self.assertEqual(planner.validate(again), [])
        again.days[0].lines[0].hours = 9
        self.assertTrue(any("left to log" in e for e in planner.validate(again)))
        again.days[0].lines[0].task = "Nope"
        self.assertTrue(any("unknown task" in e for e in planner.validate(again)))


class ConfigTests(unittest.TestCase):
    def test_dotenv_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text('ODOO_URL="https://x"\nuser: bob\n# c\nexport ODOO_PASSWORD=p w\n')
            self.assertEqual(config.load_dotenv(p), {"ODOO_URL": "https://x", "user": "bob", "ODOO_PASSWORD": "p w"})

    def test_load_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.toml").write_text('[odoo]\nproject="P"\n[git]\nauthor="me"\n[[git.repos]]\npath="repo"\n')
            (base / ".env").write_text("ODOO_URL=https://o\nODOO_USER=u\nODOO_PASSWORD=p\n")
            s = config.load_settings(str(base / "config.toml"))
            self.assertEqual(s.project, "P")
            self.assertEqual(s.repos[0].label, "repo")
            self.assertEqual(s.reminder_cron, "0 17 * * 5")
            self.assertTrue((base / "plans").is_dir())
            (base / ".env").write_text("ODOO_URL=https://o\n")
            with self.assertRaises(config.ConfigError):
                config.load_settings(str(base / "config.toml"))


class AttendanceTests(unittest.TestCase):
    def test_real_export_if_present(self):
        path = Path(__file__).resolve().parents[1] / "Foglio_presenze.xlsx"
        if not path.exists():
            self.skipTest("no attendance export in the project folder")
        att = read_attendance(path)
        self.assertGreater(len(att), 200)
        self.assertEqual(att.get("2026-02-16"), 7.0)
        self.assertEqual(att.get("2026-09-03"), 0.0)

    def test_missing_file(self):
        self.assertEqual(read_attendance(Path("/nonexistent.xlsx")), {})


if __name__ == "__main__":
    unittest.main()


class ProfileTests(unittest.TestCase):
    def test_focus_roundtrip(self):
        from odoo_timesheet import profile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "profile.md"
            self.assertEqual(profile.read_focus(p), "")
            profile.write_focus(p, "payments refund flow")
            self.assertIn("payments refund flow", profile.read_focus(p))
            self.assertIn("## Role", p.read_text())
            profile.write_focus(p, "dashboard stats")
            txt = p.read_text()
            self.assertNotIn("payments refund flow", txt)
            self.assertEqual(txt.count("## Current focus"), 1)

    def test_claude_answer_formats(self):
        from odoo_timesheet import summarize
        self.assertEqual(summarize._extract_json('{"2026-10-12#1": {"task": "Dev", "description": "x"}}')["2026-10-12#1"]["task"], "Dev")


class WizardTests(unittest.TestCase):
    def test_generated_files(self):
        from odoo_timesheet import setup_wizard as w
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            w.write_env(base, {"ODOO_URL": "https://o", "ODOO_USER": "u", "ODOO_PASSWORD": "p"})
            self.assertEqual(oct((base / ".env").stat().st_mode)[-3:], "600")
            self.assertEqual(config.load_dotenv(base / ".env")["ODOO_USER"], "u")
            w.write_profile_md(base, "designer", "mockups; icons", "new landing page")
            txt = (base / "profile.md").read_text()
            self.assertIn("- mockups\n- icons", txt)
            self.assertIn("new landing page", txt)
            self.assertTrue(w.write_tasks_md(base, "P", ["Design", "Dev"]))
            self.assertFalse(w.write_tasks_md(base, "P", ["Design"]))   # never overwrites a shared file
            self.assertIn("## Design", (base / "tasks.md").read_text())
            self.assertEqual(w._toml('a "q" b'), '"a \\"q\\" b"')

    def test_piped_answers(self):
        import io, sys
        from odoo_timesheet import setup_wizard as w
        old = sys.stdin
        try:
            sys.stdin = io.StringIO("\nsecret\n")
            self.assertEqual(w.ask("x", "dflt"), "dflt")
            self.assertEqual(w.ask("pw", secret=True), "secret")
            with self.assertRaises(SystemExit):
                w.ask("more")
        finally:
            sys.stdin = old
