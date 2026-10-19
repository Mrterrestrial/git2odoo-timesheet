# git2odoo-timesheet

Suggests what to write in your Odoo timesheets from your git commits, lets you review and
edit the suggestions on a local web page, and fills Odoo with one click. A weekly cron job
reminds you on Friday at 17:00 with the suggestions already prepared.

Python 3.11+ and git only. No third-party packages.

## How it works

1. **Which days are missing.** Every weekday between `start_date` and today (bounded by
   `lookback_days`) is expected to have `hours_per_day` hours, minus weekends, public holidays
   (built-in Italian list, `extra_holidays`, holidays configured in Odoo) and your time off in Odoo.
   If an HR attendance export (`attendance_file`) is present, its hours per day win.
   A day is missing when Odoo has fewer hours than expected.
2. **What you did.** `git log --author=<you>` is read in every configured repository, grouped by day.
   Commits are mapped to an Odoo task with the `[[tasks.rules]]` regexes, then the repo's
   `default_task`, then `[odoo].default_task`. A day with commits on two tasks gets two lines, hours
   split by commit count.
3. **Descriptions.** With `summarizer.mode = "auto"` the `claude` CLI writes one or two business-readable
   sentences per line from the commit messages (one call for the whole plan). Without it, or if it
   fails, the commit subjects are cleaned and joined. Days without commits get a "Work in progress on…"
   guess and are marked **needs your input**.
4. **Review and fill.** The plan is saved in `plans/` and shown at http://127.0.0.1:8765. Edit
   task, hours and text, untick days, press **Fill Odoo**. Lines are created on the configured project
   with your employee; a day that already has enough hours in Odoo is skipped, never duplicated.

## Setup

```bash
./start.sh
```

The first run starts a short wizard (`python3 timesheet.py init`): it asks for the Odoo URL, database,
login and password (verified live), the project, a default task, your repositories, calendar details,
your role and current focus, and the reminder schedule. It writes `.env` (mode 600), `config.toml`,
`profile.md` and, if absent, a `tasks.md` skeleton listing the project's tasks, then offers to install
the cron reminder. Later runs of `start.sh` show the status and open the review page.
All four generated files are personal and git-ignored; `*.example.*` files show their shape.
Re-run the wizard with `python3 timesheet.py init --force`.

## Requirements and compatibility

* **Odoo**: any edition (Community, Enterprise, Odoo.sh, Odoo Online) with the Timesheets app
  (`hr_timesheet`) and XML-RPC access, versions 13 to 18. Only standard models and fields are used:
  `account.analytic.line` (date, project, task, employee, hours, description), `project.project`,
  `project.task`, optionally `hr.leave` and `resource.calendar.leaves`. If your login uses two-factor
  authentication, put an Odoo API key in `ODOO_PASSWORD` instead of the password.
* **Company rules Odoo enforces** (locked periods, approval workflows, custom mandatory fields) are not
  bypassed: Odoo's error is shown on the page and nothing is written for that day.
* **Workstation**: Python 3.11+, git. The weekly reminder uses `crontab`, `notify-send` and `xdg-open`,
  so it is Linux-desktop only; every command also works by hand on macOS or Windows.
* **Optional**: the `claude` CLI for well-written descriptions; an attendance export in the
  dipendentincloud.it "Foglio presenze" format for exact hours per day.

## Commands

| Command | What it does |
|---|---|
| `init [--force]` | First-run wizard: credentials, project, repositories, profile. `start.sh` runs it when needed. |
| `status` | Missing days in the period and existing lines without task or description. Exit code 1 when something is missing. |
| `suggest [--from D --to D] [--summarizer auto\|claude\|simple]` | Builds and saves a plan, prints it. |
| `open` | Opens the review page (starts the local server if needed). |
| `focus ["text"]` | Show or set what you are working on these days (profile.md). |
| `fill [--day D] [--dry-run]` | Fills Odoo from the latest plan without the web page. |
| `weekly` | What cron runs: check, suggest, start the page, send a desktop notification with an **Open** button. |
| `serve [--port N]` | Runs the review page in the foreground. |
| `install-cron` / `show-cron` / `uninstall-cron` | Manage the reminder line (`[reminder].cron`, default Friday 17:00). |

All commands accept `--config path/to/config.toml`; otherwise `config.toml` next to `timesheet.py` is used.

## Files

```
timesheet.py            entry point
start.sh                first run: wizard; later: status + review page
odoo_timesheet/
  config.py             .env + config.toml -> Settings
  odoo_client.py        XML-RPC: project, tasks, timesheets, time off, holidays
  workdays.py           expected hours per day (holidays, time off, attendance export)
  attendance.py         reader for the dipendentincloud "Foglio presenze" export (optional)
  gitlog.py             commits per day per repository
  summarize.py          descriptions: claude CLI or plain commit subjects
  planner.py            plan = list of days with lines; build / validate / save / load
  filler.py             creates the lines in Odoo
  web.py                local review page
  jobs.py               status, weekly job, server start
  notify.py             notify-send / xdg-open
  cron.py               crontab line management
  cli.py                argparse front end
  setup_wizard.py       first-run wizard (init)
  profile.py            profile.md / tasks.md helpers
profile.md, tasks.md    your profile and the shared task catalogue (optional)
plans/                  generated plans (JSON), `latest` points to the current one
logs/                   weekly.log, serve.log
tests/                  offline unit tests: python3 -m unittest discover -s tests
```

## Profile and task catalogue

Two optional markdown files steer the suggestions:

* `profile.md` (personal, see `profile.example.md`): your role, responsibilities, **current focus** and writing
  preferences. Update the focus any time with `python3 timesheet.py focus "refund flow and dashboard stats"`
  or the box at the top of the review page. Days without commits are then described from this focus.
* `tasks.md` (shared with colleagues, see `tasks.example.md`): what belongs to each Odoo task of the project.
  With it, the engine may move a line to a better task than the regex rules guessed, so a designer, a backend
  developer and a devops engineer can share the same task list and still get correct mappings.

Both are sent to the `claude` call only; the plain fallback ignores them.

## Adapting it

* **Another project or person:** edit `config.toml` and `.env` only. Nothing in the code refers to a user.
* **More repositories:** add `[[git.repos]]` entries. A repo that is not cloned is skipped with a warning.
* **Task mapping:** `[[tasks.rules]]` are tried in order on subject + body; keep the most specific first.
* **No attendance export:** delete `attendance_file`; hours come from `hours_per_day`, Odoo time off and holidays.
* **Other countries:** set `holidays = ""` and list closing days in `extra_holidays`, or add a provider in `workdays.py`.
* **Cron and the desktop:** the job sets `DISPLAY`/`DBUS_SESSION_BUS_ADDRESS` itself so the notification reaches
  your session. Check `logs/weekly.log` if nothing shows up.
