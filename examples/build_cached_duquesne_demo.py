"""Build the cached Duquesne 13F specimen without network access or credentials.

The committed JSON is a cached rendering input captured from public filing data.
It is intentionally point-in-time, not a live filing fetch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from manager13f import build, verify


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "duquesne_cached_2026-03-31.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "duquesne_cached_demo.xlsx")
    args = parser.parse_args()

    data = json.loads(FIXTURE.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build.build(data, str(args.out))
    ok, detail = verify.structural_check(str(args.out))
    if not ok:
        raise RuntimeError(f"offline workbook failed structural gate: {detail}")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
