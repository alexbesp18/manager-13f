"""Offline, committed-fixture demo contract. No network and no credentials."""

from __future__ import annotations

import json
from pathlib import Path

from manager13f import build, verify


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "duquesne_cached_2026-03-31.json"


def test_cached_duquesne_fixture_builds_a_structurally_clean_workbook(tmp_path):
    data = json.loads(FIXTURE.read_text())
    out = tmp_path / "duquesne_cached_demo.xlsx"

    build.build(data, str(out))

    assert data["meta"]["manager"] == "Duquesne Family Office LLC"
    assert out.is_file()
    assert verify.structural_check(str(out))[0]
