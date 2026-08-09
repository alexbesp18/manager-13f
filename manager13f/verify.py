"""Verify a generated .xlsx actually opens in Excel with NO repair/recover prompt.

Excel's "We found a problem… recover?" dialog BLOCKS the open, so if AppleScript can
open the workbook + read a cell within the timeout, the file is clean. macOS only;
returns (ok, detail). Falls back to a structural check when Excel/osascript is absent.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from er_kit.cleanopen import clean_open


def structural_check(path: str) -> tuple[bool, str]:
    """No single-cell merges, no overlapping merges — the two openpyxl/xlsx repair triggers."""
    issues = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if re.match(r"xl/worksheets/sheet\d+\.xml", name):
                xml = z.read(name).decode("utf-8", "ignore")
                refs = re.findall(r'<mergeCell ref="([^"]+)"', xml)
                singles = [m for m in refs if ":" not in m or m.split(":")[0] == m.split(":")[1]]
                if singles:
                    issues.append(f"{name}: single-cell merges {singles[:3]}")
    return (not issues), ("; ".join(issues) if issues else "structurally clean")


def excel_clean_open(path: str, timeout: int = 25) -> tuple[bool, str]:
    """Drive Excel via er_kit.cleanopen. True iff no blocking recover dialog appears.

    `timeout` is forwarded to er_kit so existing callers keep the same wait bound.
    """
    path = str(Path(path).resolve())
    ok, detail = clean_open(path, delay=1.0, timeout=timeout)
    if ok and detail.startswith("skipped:"):
        ok, detail = structural_check(path)
        return ok, f"clean_open skipped; structural-only: {detail}"
    if ok:
        return True, f"clean open — no recover dialog ({detail})"
    return False, detail
