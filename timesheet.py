#!/usr/bin/env python3
"""Entry point. Run `python3 timesheet.py --help`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from odoo_timesheet.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
