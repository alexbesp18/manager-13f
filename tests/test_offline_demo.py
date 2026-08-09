"""Offline, committed-fixture demo contract. No network and no credentials."""

from __future__ import annotations

import json
from pathlib import Path

from manager13f import build, verify


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "scion_cached_2025-09-30.json"


def test_cached_scion_fixture_builds_a_structurally_clean_workbook(tmp_path):
    data = json.loads(FIXTURE.read_text())
    out = tmp_path / "scion_cached_demo.xlsx"

    build.build(data, str(out))

    assert data["meta"]["manager"] == "Scion Asset Management, LLC"
    assert out.is_file()
    assert verify.structural_check(str(out))[0]
