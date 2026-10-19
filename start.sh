#!/usr/bin/env bash
# First run: asks for your Odoo login and project details and creates your personal files.
# Later runs: shows the status and opens the review page.
set -e
cd "$(dirname "$0")"
if [ ! -f .env ] || [ ! -f config.toml ]; then
  python3 timesheet.py init
fi
python3 timesheet.py status || true
python3 timesheet.py open
