"""Optional reader for the attendance export (Foglio presenze, dipendentincloud.it).

One sheet per month, a header row with day labels like "Lun 5", and a row whose first
cell is the configured label (default "Presenza effettiva") with the hours worked per day.
Only the standard library is used, so openpyxl is not required.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def read_attendance(path: Path, row_label: str = "Presenza effettiva") -> dict[str, float]:
    """Return {ISO date: hours worked} for every day found in the workbook."""
    if not path or not Path(path).exists():
        return {}
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    out: dict[str, float] = {}
    for name in z.namelist():
        if not re.match(r"xl/worksheets/sheet\d+\.xml$", name):
            continue
        rows: dict[int, dict[str, str]] = {}
        for row in ET.fromstring(z.read(name)).iter("{%s}row" % NS["m"]):
            cells = {}
            for c in row.findall("m:c", NS):
                v = c.find("m:v", NS)
                if v is None:
                    continue
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                cells[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
            if cells:
                rows[int(row.get("r"))] = cells
        title = " ".join(str(v) for r in sorted(rows)[:3] for v in rows[r].values())
        m = re.search(r"\b([A-Za-z]+)\s+(\d{4})\b", title)
        if not m or m.group(1).lower() not in MONTHS:
            continue
        month, year = MONTHS[m.group(1).lower()], int(m.group(2))
        header = next((r for r in rows.values() if sum(1 for v in r.values() if re.match(r"^[A-Za-z]{3} \d{1,2}$", str(v))) >= 20), None)
        actual = next((r for r in rows.values() if str(r.get("A", "")).strip().lower() == row_label.lower()), None)
        if not header or not actual:
            continue
        for col, label in header.items():
            dm = re.match(r"^[A-Za-z]{3} (\d{1,2})$", str(label))
            if dm and col in actual:
                try:
                    day = dt.date(year, month, int(dm.group(1)))
                except ValueError:
                    continue
                out[day.isoformat()] = float(actual[col])
    return out
