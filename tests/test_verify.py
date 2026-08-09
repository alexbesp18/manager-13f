from manager13f import verify


def test_excel_clean_open_delegates_to_er_kit_cleanopen(monkeypatch, tmp_path):
    workbook = tmp_path / "report.xlsx"
    workbook.write_bytes(b"not used")
    called = {}

    def fake_clean_open(path, delay, timeout):
        called["path"] = path
        called["delay"] = delay
        called["timeout"] = timeout
        return True, "CLEAN OPEN OK | workbook=report.xlsx | sheets=1"

    monkeypatch.setattr(verify, "clean_open", fake_clean_open)
    ok, detail = verify.excel_clean_open(str(workbook), timeout=7)

    assert ok
    assert called == {"path": str(workbook.resolve()), "delay": 1.0, "timeout": 7}
    assert detail.startswith("clean open")
    assert "no recover dialog" in detail
    assert "CLEAN OPEN OK" in detail


def test_excel_clean_open_keeps_structural_fallback_when_cleanopen_skips(monkeypatch, tmp_path):
    workbook = tmp_path / "report.xlsx"
    workbook.write_bytes(b"not used")

    monkeypatch.setattr(verify, "clean_open", lambda path, delay, timeout: (True, "skipped: not macOS"))
    monkeypatch.setattr(verify, "structural_check", lambda path: (True, "structurally clean"))

    ok, detail = verify.excel_clean_open(str(workbook))

    assert ok
    assert detail == "clean_open skipped; structural-only: structurally clean"


def test_excel_clean_open_passes_through_cleanopen_failure(monkeypatch, tmp_path):
    workbook = tmp_path / "report.xlsx"
    workbook.write_bytes(b"not used")
    monkeypatch.setattr(
        verify,
        "clean_open",
        lambda path, delay, timeout: (False, "repair-dialog/workbook failure: corrupt file"),
    )

    ok, detail = verify.excel_clean_open(str(workbook))

    assert ok is False
    assert detail == "repair-dialog/workbook failure: corrupt file"
