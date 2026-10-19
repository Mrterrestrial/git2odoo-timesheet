"""Local review page: edit the suggestions and fill Odoo with one click."""
from __future__ import annotations

import datetime as dt
import html
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import jobs
from .config import Settings
from .filler import fill
from .odoo_client import OdooClient, OdooError
from .planner import Plan, build_plan, load_plan, save_plan, validate
from .profile import read_focus, write_focus

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#687385;--line:#dfe3ea;--accent:#2f6fed;--ok:#1d8f4e;--warn:#c47d00;--err:#c0392b;--badge:#eef1f6}
@media(prefers-color-scheme:dark){:root{--bg:#141820;--card:#1c2230;--fg:#e8ecf3;--muted:#98a2b5;--line:#2c3443;--accent:#6b9bff;--badge:#262e3d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1060px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0 2px}.sub{color:var(--muted);margin:0 0 14px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);z-index:2}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}button.danger{color:var(--err)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.card.filled{opacity:.6}.card.needs_review{border-color:var(--warn)}.card.error{border-color:var(--err)}
.head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:8px}.head label{font-weight:600;font-size:16px}
.meta{color:var(--muted);font-size:13px}.badge{font-size:12px;padding:2px 8px;border-radius:999px;background:var(--badge)}
.badge.needs_review{background:var(--warn);color:#fff}.badge.filled{background:var(--ok);color:#fff}.badge.error{background:var(--err);color:#fff}
.line{display:grid;grid-template-columns:200px 90px 1fr;gap:8px;margin:6px 0}
select,input,textarea{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);width:100%}
textarea{min-height:62px;resize:vertical}details{margin-top:6px;color:var(--muted);font-size:13px}details li{margin:2px 0}
.msg{padding:10px 14px;border-radius:10px;margin:10px 0;background:var(--badge)}.msg.err{border:1px solid var(--err)}.msg.ok{border:1px solid var(--ok)}
.note{color:var(--warn);font-size:13px}@media(max-width:720px){.line{grid-template-columns:1fr 1fr}.line textarea{grid-column:1/-1}}
"""


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def render(s: Settings, plan: Plan | None, message: str = "", kind: str = "", errors: list[str] | None = None) -> str:
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Odoo timesheet</title><style>{CSS}</style></head><body><main>"]
    parts.append(f"<h1>Odoo timesheet · {esc(s.project)}</h1>")
    if message:
        parts.append(f"<div class='msg {kind}'>{esc(message)}</div>")
    if errors:
        parts.append("<div class='msg err'><b>Please fix before filling:</b><ul>" + "".join(f"<li>{esc(e)}</li>" for e in errors) + "</ul></div>")
    focus = read_focus(s.profile_file)
    parts.append("<details class='card' style='padding:10px 14px'><summary><b>What I'm working on these days</b> "
                 "<span class='meta'>(used for task choice and wording; edit, save, then rebuild)</span></summary>"
                 f"<form method='post' action='/focus'><textarea name='focus' style='margin:8px 0'>{esc(focus)}</textarea>"
                 "<button type='submit'>Save focus</button></form></details>")
    pending = plan.pending if plan else []
    if not plan or not pending:
        if plan:
            parts.append(f"<p class='sub'>Plan {esc(plan.id)} · {esc(plan.start)} → {esc(plan.end)} · every day is filled or skipped.</p>")
        parts.append("<div class='card'><p>Nothing to fill right now.</p>"
                     "<form method='post' action='/refresh'><button class='primary'>Check Odoo and build new suggestions</button></form></div>")
        if plan and plan.days:
            parts.append("<details><summary>Last plan</summary><ul>" + "".join(
                f"<li>{esc(d.date)} · {esc(d.status)} · {esc(d.note)}</li>" for d in plan.days) + "</ul></details>")
        parts.append("</main></body></html>")
        return "".join(parts)

    total = sum(d.hours for d in pending if d.include)
    parts.append(f"<p class='sub'>Plan {esc(plan.id)} generated {esc(plan.created_at.replace('T', ' '))} · period {esc(plan.start)} → {esc(plan.end)} · "
                 f"descriptions by <b>{esc(plan.summarizer)}</b> · user {esc(s.odoo_user)}</p>")
    for n in plan.notes:
        parts.append(f"<p class='note'>{esc(n)}</p>")
    parts.append("<form method='post' action='/save' id='f'>")
    parts.append(f"<div class='bar'><button type='submit' formaction='/save'>Save changes</button>"
                 f"<button type='submit' formaction='/fill' class='primary' onclick=\"return confirm('Create the ticked days in Odoo?')\">"
                 f"Fill Odoo ({len([d for d in pending if d.include])} days, {total:g} h)</button>"
                 f"<span class='meta'>Untick a day to leave it out. Edit task, hours and text freely.</span></div>")
    tasks = sorted(plan.tasks)
    for i, d in enumerate(plan.days):
        if d.status in ("filled", "skipped"):
            parts.append(f"<div class='card filled'><div class='head'><label>{esc(d.weekday)} {esc(d.date)}</label>"
                         f"<span class='badge {esc(d.status)}'>{esc(d.status)}</span><span class='meta'>{esc(d.note)}</span></div></div>")
            continue
        parts.append(f"<div class='card {esc(d.status)}'><div class='head'>"
                     f"<label><input type='checkbox' name='inc_{i}' {'checked' if d.include else ''}> {esc(d.weekday)} {esc(d.date)}</label>"
                     f"<span class='badge {esc(d.status)}'>{'needs your input' if d.status == 'needs_review' else esc(d.status)}</span>"
                     f"<span class='meta'>expected {d.expected_hours:g} h · {esc(d.reason)}"
                     f"{' · ' + esc(d.note) if d.note else ''}</span></div>")
        for j, l in enumerate(d.lines):
            opts = "".join(f"<option {'selected' if t == l.task else ''}>{esc(t)}</option>" for t in tasks)
            parts.append(f"<div class='line'><select name='task_{i}_{j}'>{opts}</select>"
                         f"<input type='number' step='0.25' min='0.25' max='24' name='hours_{i}_{j}' value='{l.hours:g}'>"
                         f"<textarea name='desc_{i}_{j}'>{esc(l.description)}</textarea></div>")
        if d.commits:
            parts.append(f"<details><summary>{len(d.commits)} commit(s)</summary><ul>" + "".join(
                f"<li><b>{esc(c['repo'])}</b> {esc(c['subject'])}</li>" for c in d.commits) + "</ul></details>")
        else:
            parts.append("<details><summary>no commits on this day</summary><p>Describe what you did, or untick the day.</p></details>")
        parts.append("</div>")
    parts.append("</form>")
    parts.append("<form method='post' action='/refresh' onsubmit=\"return confirm('Discard unsaved edits and rebuild suggestions from Odoo and git?')\">"
                 "<button class='danger'>Rebuild suggestions</button></form>")
    parts.append("</main></body></html>")
    return "".join(parts)


def apply_form(plan: Plan, form: dict[str, list[str]]) -> None:
    for i, d in enumerate(plan.days):
        if d.status in ("filled", "skipped"):
            continue
        d.include = f"inc_{i}" in form
        for j, l in enumerate(d.lines):
            l.task = form.get(f"task_{i}_{j}", [l.task])[0]
            try:
                l.hours = float(form.get(f"hours_{i}_{j}", [l.hours])[0])
            except ValueError:
                pass
            l.description = form.get(f"desc_{i}_{j}", [l.description])[0].strip()
        if d.status == "needs_review" and d.lines and d.lines[0].description and not d.lines[0].description.startswith("Work in progress"):
            d.status = "suggested"


class Handler(BaseHTTPRequestHandler):
    server: "ReviewServer"

    def log_message(self, fmt, *args):  # quieter log
        print(f"{dt.datetime.now():%H:%M:%S} {self.address_string()} {fmt % args}", flush=True)

    def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to="/"):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        s = self.server.settings
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            return self._send("ok", ctype="text/plain")
        if path == "/plan.json":
            plan = load_plan(s)
            return self._send(json.dumps(plan.to_dict() if plan else None, indent=2), ctype="application/json")
        if path != "/":
            return self._send("not found", 404, "text/plain")
        return self._send(render(s, load_plan(s)))

    def do_POST(self):
        s = self.server.settings
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        with self.server.lock:
            try:
                if path == "/focus":
                    write_focus(s.profile_file, form.get("focus", [""])[0])
                    return self._send(render(s, load_plan(s), "Focus saved. Use 'Rebuild suggestions' to apply it.", "ok"))
                if path == "/refresh":
                    odoo = OdooClient.from_settings(s)
                    start, end = jobs.period(s)
                    plan = build_plan(s, odoo, start, end, log=lambda m: print(m, flush=True))
                    save_plan(plan, s)
                    return self._redirect("/")
                plan = load_plan(s)
                if not plan:
                    return self._redirect("/")
                apply_form(plan, form)
                save_plan(plan, s, make_latest=False)
                if path == "/save":
                    return self._send(render(s, plan, "Changes saved.", "ok"))
                if path == "/fill":
                    errors = validate(plan)
                    if errors:
                        return self._send(render(s, plan, errors=errors))
                    odoo = OdooClient.from_settings(s)
                    results = fill(plan, s, odoo, log=lambda m: print(m, flush=True))
                    ok = [r for r in results if r["ok"]]
                    bad = [r for r in results if not r["ok"]]
                    msg = f"Created {sum(len(r['ids']) for r in ok)} line(s) for {len(ok)} day(s) in Odoo."
                    if bad:
                        msg += " Skipped: " + "; ".join(f"{r['date']} ({r['message']})" for r in bad)
                    return self._send(render(s, plan, msg, "ok" if not bad else "err"))
                return self._send("not found", 404, "text/plain")
            except (OdooError, ValueError, RuntimeError) as e:
                return self._send(render(s, load_plan(s), f"Error: {e}", "err"))


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, settings: Settings):
        super().__init__((settings.web_host, settings.web_port), Handler)
        self.settings = settings
        self.lock = threading.Lock()


def serve(settings: Settings) -> None:
    srv = ReviewServer(settings)
    print(f"review page at {settings.web_url}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
