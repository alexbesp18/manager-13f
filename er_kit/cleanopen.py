"""Vendored macOS clean-open gate.

Derived from the local equity-research-kit cleanopen helper, retained here so
this public package has no sibling/path dependency. This module uses only the
Python standard library.

Xlsxwriter files can trip Excel's repair dialog via single-cell merges / 00-alpha dxf;
this verifies a real clean open.
No-op success on non-macOS (returns ok with a note) so callers/tests don't break in CI.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

# Path + delay + expected name are passed as osascript ARGV items (not interpolated into the
# script text), so a path containing quotes/backslashes can't break or inject into the AppleScript.
# The workbook is resolved BY NAME after the open — never via `active workbook` — so a different
# frontmost book (e.g. a user's Book1) can neither validate in place of the target nor get closed.
# If the expected name can't be resolved, wbName stays ABSENT and the Python side fails the gate.
_SCRIPT = """
on run argv
  set f to POSIX file (item 1 of argv)
  set d to (item 2 of argv) as number
  set expectedName to item 3 of argv
  tell application "Microsoft Excel"
    activate
    open f
    delay d
    set wbName to "ABSENT"
    set sheetCount to 0
    -- A busy Excel (user documents open) can take extra seconds to register the new
    -- workbook: poll the by-name resolution briefly rather than trusting one delay.
    set attemptsLeft to 16
    repeat while attemptsLeft > 0
      try
        set targetBook to workbook expectedName
        set wbName to name of targetBook
        set sheetCount to count of sheets of targetBook
        close targetBook saving no
        exit repeat
      on error
        delay 0.5
        set attemptsLeft to attemptsLeft - 1
      end try
    end repeat
    return "CLEAN OPEN OK | workbook=" & wbName & " | sheets=" & sheetCount
  end tell
end run
"""


def _classify_failure(returncode: int, out: str, err: str) -> str:
    raw = err or out or f"osascript rc={returncode}"
    text = raw.lower()
    if "-50" in text or "active workbook" in text:
        return f"environment/automation failure: {raw}"
    if "repair" in text or "recover" in text or "corrupt" in text:
        return f"repair-dialog/workbook failure: {raw}"
    return raw


def _reported_workbook_name(out: str) -> str | None:
    prefix = "CLEAN OPEN OK | workbook="
    if not out.startswith(prefix):
        return None
    name, separator, _ = out[len(prefix) :].rpartition(" | sheets=")
    return name if separator else None


def clean_open(path: str, delay: float = 2.0, timeout: float = 60.0) -> tuple[bool, str]:
    """Open `path` in Excel and close it. Returns (ok, message).

    ok=True means Excel opened the workbook directly (no repair dialog).
    On non-macOS, returns (True, 'skipped: not macOS').
    """
    if platform.system() != "Darwin":
        return True, "skipped: not macOS"
    # Absolutize BEFORE the existence check and the AppleScript call: sandboxed Excel
    # resolves relative paths against its own container home, so a cwd-relative path
    # that exists for Python still pops the blocking "couldn't find" modal in Excel.
    path = str(Path(path).expanduser().resolve())
    if not Path(path).is_file():
        # Never hand Excel a missing path: it pops a modal "couldn't find" alert that
        # blocks every subsequent programmatic open until a human dismisses it.
        return False, f"file does not exist: {path}"
    try:
        res = subprocess.run(
            ["osascript", "-e", _SCRIPT, path, str(delay), Path(path).name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if res.returncode == 0 and "CLEAN OPEN OK" in out:
            expected = Path(path).name
            actual = _reported_workbook_name(out)
            accepted_names = {expected.casefold(), Path(expected).stem.casefold()}
            if actual is None or actual.casefold() not in accepted_names:
                return (
                    False,
                    f"workbook name mismatch: expected={expected!r} actual={actual!r}",
                )
            return True, out
        return False, _classify_failure(res.returncode, out, err)
    except FileNotFoundError:
        return True, "skipped: osascript not found"
    except subprocess.TimeoutExpired:
        return False, "timeout opening Excel"
