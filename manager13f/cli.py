"""manager13f CLI — turn any 13F filer into a one-page intelligence sheet.

manager13f "Example Capital"
manager13f --cik 0000000001
manager13f --cik 0001536411 -o output/duquesne.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import build, pipeline, verify

OUTPUT = Path(__file__).resolve().parent.parent / "output"


def _run_deep(a, target) -> int:
    """Deep mode: multi-quarter evolution dossier (matrix + flows + performance)."""
    from . import deep, deep_build

    data = deep.run_deep(target, benchmark=a.benchmark, quarters=a.quarters)
    m = data["meta"]
    print(
        f"✓ DEEP {m['manager']} | {m['n_quarters']} quarters "
        f"({m['first_period']}→{m['latest_period']}) | universe {m['universe_size']}",
        file=sys.stderr,
    )
    if m["unresolved_cusips"]:
        print(f"  ⚠ unresolved CUSIPs: {m['unresolved_cusips']}", file=sys.stderr)
    if m["price_errors"]:
        print(f"  ⚠ no price history: {[e['ticker'] for e in m['price_errors']]}", file=sys.stderr)
    if m.get("scale_corrections"):
        print(f"  ⓘ value-scale corrected: {m['scale_corrections']}", file=sys.stderr)
    if m.get("excluded_filings"):
        print(
            f"  ⚠ excluded garbled filings: {[e['period'] for e in m['excluded_filings']]}",
            file=sys.stderr,
        )
    if m["px_quality_flags"]:
        print(
            f"  ⚠ filing-px divergence (post-correction >50x): {[p['ticker'] for p in m['px_quality_flags']]}",
            file=sys.stderr,
        )

    syn = json.loads(Path(a.synthesis).read_text()) if a.synthesis else None
    slug = "".join(c if c.isalnum() else "_" for c in m["manager"].lower())[:40]
    out = a.out or str(OUTPUT / f"{slug}_evolution.xlsx")
    OUTPUT.mkdir(exist_ok=True)
    deep_build.build_deep(data, out, synthesis=syn)
    print(f"✓ wrote {out}", file=sys.stderr)

    if not a.no_verify:
        ok, detail = verify.excel_clean_open(out)
        print(f"{'✓' if ok else '✗'} clean-open: {detail}", file=sys.stderr)
        if not ok:
            return 1
    print(out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="manager13f", description=__doc__)
    ap.add_argument("manager", nargs="?", help="manager name (EDGAR will resolve the CIK)")
    ap.add_argument("--cik", help="CIK number (skips name lookup)")
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("-o", "--out", help="output .xlsx path")
    ap.add_argument("--synthesis", help="path to a synthesis.json (theme/WOWs/analyst patches)")
    ap.add_argument("--no-verify", action="store_true", help="skip the Excel clean-open check")
    ap.add_argument("--deep", action="store_true", help="multi-quarter evolution dossier")
    ap.add_argument("--quarters", type=int, help="deep mode: cap to the N most recent quarters")
    a = ap.parse_args(argv)

    target = a.cik or a.manager
    if not target:
        ap.error("provide a manager name or --cik")

    if a.deep:
        return _run_deep(a, target)

    data = pipeline.run(target, benchmark=a.benchmark)
    m = data["meta"]
    print(
        f"✓ {m['manager']} | {m['latest_period']} | {m['n_positions']} positions | "
        f"options_book={m['is_options_book']}",
        file=sys.stderr,
    )
    if m["unresolved_cusips"]:
        print(f"  ⚠ unresolved CUSIPs (ticker-resolver agent): {m['unresolved_cusips']}", file=sys.stderr)
    if m["enrich_errors"]:
        print(f"  ⚠ no market data: {[e['ticker'] for e in m['enrich_errors']]}", file=sys.stderr)

    syn = json.loads(Path(a.synthesis).read_text()) if a.synthesis else None
    slug = "".join(c if c.isalnum() else "_" for c in m["manager"].lower())[:40]
    out = a.out or str(OUTPUT / f"{slug}_{m['latest_period']}.xlsx")
    OUTPUT.mkdir(exist_ok=True)
    build.build(data, out, synthesis=syn)
    print(f"✓ wrote {out}", file=sys.stderr)

    if not a.no_verify:
        ok, detail = verify.excel_clean_open(out)
        print(f"{'✓' if ok else '✗'} clean-open: {detail}", file=sys.stderr)
        if not ok:
            return 1
    print(out)  # stdout = the artifact path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
