"""Pure-transform tests for deep mode (no network). Replay-exact contract.

A 3-quarter synthetic history exercises: matrix actions (New/Add/Hold/Trim), exit
detection, lifecycle flags, flow forward-returns, and the performance attribution math
incl. the filing-px divergence (unit/CUSIP) guard.
"""

from __future__ import annotations

import pandas as pd

from manager13f import deep
from manager13f.edgar import Filing, Position

P1, P2, P3 = "2024-03-31", "2024-06-30", "2024-09-30"


def _pos(name, cusip, value, shares):
    return Position(
        name=name, cusip=cusip, title_class="COM", value=value, shares=shares, sh_type="SH", put_call=""
    )


def _history():
    # AAA: held all 3 quarters (added in Q2). BBB: held Q1-Q2, exited Q3. CCC: new Q2, trimmed Q3.
    q1 = Filing(
        "9",
        "a1",
        P1,
        "2024-05-15",
        [_pos("AAA CORP", "AAA", 10_000_000, 100_000), _pos("BBB INC", "BBB", 5_000_000, 100_000)],
    )
    q2 = Filing(
        "9",
        "a2",
        P2,
        "2024-08-14",
        [
            _pos("AAA CORP", "AAA", 14_000_000, 140_000),
            _pos("BBB INC", "BBB", 5_000_000, 100_000),
            _pos("CCC LTD", "CCC", 8_000_000, 80_000),
        ],
    )
    q3 = Filing(
        "9",
        "a3",
        P3,
        "2024-11-14",
        [_pos("AAA CORP", "AAA", 15_000_000, 140_000), _pos("CCC LTD", "CCC", 4_000_000, 40_000)],
    )
    return [q1, q2, q3]


MAPPING = {"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"}


def test_matrix_actions_and_lifecycle():
    m = deep.build_matrix(_history(), MAPPING)
    assert m["periods"] == [P1, P2, P3]
    by_tk = {r["ticker"]: r for r in m["rows"]}

    aaa = by_tk["AAA"]
    assert aaa["cells"][P1]["action"] == "New"  # first filing => initiated
    assert aaa["cells"][P2]["action"] == "Add"  # 100k -> 140k
    assert aaa["cells"][P3]["action"] == "Hold"  # 140k -> 140k
    assert aaa["still_held"] is True
    assert aaa["n_quarters_held"] == 3
    assert aaa["first_seen"] == P1 and aaa["last_seen"] == P3

    bbb = by_tk["BBB"]
    assert bbb["still_held"] is False
    assert bbb["last_seen"] == P2  # gone in Q3

    ccc = by_tk["CCC"]
    assert ccc["cells"][P2]["action"] == "New"
    assert ccc["cells"][P3]["action"] == "Trim"  # 80k -> 40k


def test_flow_ledger_detects_exit_and_counts():
    flows = deep.flow_ledger(_history(), MAPPING)
    exits = [e for e in flows if e["action"] == "Exit"]
    assert {e["ticker"] for e in exits} == {"BBB"}
    # Q2 has AAA Add + CCC New; Q3 has CCC Trim + BBB Exit
    q2 = [e for e in flows if e["period"] == P2]
    assert {(e["ticker"], e["action"]) for e in q2} == {("AAA", "Add"), ("CCC", "New")}


def test_quarter_summaries_activity():
    qs = deep.quarter_summaries(_history())
    assert [q["period"] for q in qs] == [P1, P2, P3]
    assert qs[0]["n_new"] == 2  # both initiated in the first filing
    assert qs[1]["n_new"] == 1  # CCC
    assert qs[1]["n_add"] == 1  # AAA
    assert qs[2]["n_exit"] == 1  # BBB
    assert qs[2]["n_trim"] == 1  # CCC


def _close_df(points):
    idx = pd.DatetimeIndex([d for d, _ in points])
    vals = [v for _, v in points]
    return pd.DataFrame({"Close": vals, "AdjClose": vals}, index=idx)


def test_performance_attribution_and_alpha():
    m = deep.build_matrix(_history(), MAPPING)
    closes = {
        "AAA": _close_df(
            [("2024-03-29", 100), ("2024-06-28", 120), ("2024-09-30", 150), ("2024-12-31", 180)]
        ),
        "BBB": _close_df([("2024-03-29", 50), ("2024-06-28", 60), ("2024-09-30", 55)]),
        "CCC": _close_df([("2024-06-28", 100), ("2024-09-30", 90), ("2024-12-31", 95)]),
    }
    bench = _close_df([("2024-03-29", 400), ("2024-06-28", 420), ("2024-09-30", 430), ("2024-12-31", 440)])
    perf = {p["ticker"]: p for p in deep.attribute_performance(m, closes, bench)}

    aaa = perf["AAA"]  # still held: entry Q1 close 100 -> now 180 = +80%
    assert aaa["still_held"] is True
    assert round(aaa["holding_return_pct"], 1) == 80.0
    assert round(aaa["bench_return_pct"], 1) == 10.0  # 400 -> 440
    assert round(aaa["alpha_pct"], 1) == 70.0

    bbb = perf["BBB"]  # closed: entry Q1 50 -> last-held Q2 60 = +20%
    assert bbb["still_held"] is False
    assert round(bbb["holding_return_pct"], 1) == 20.0
    assert round(bbb["bench_return_pct"], 1) == 5.0  # 400 -> 420


def test_filing_px_divergence_flag():
    # only an order-of-magnitude (>50x) implied-vs-actual gap flags (a true unit/CUSIP error);
    # a <=20x gap is a stock split (yfinance Close is split-adjusted) and must NOT flag.
    m = deep.build_matrix(_history(), MAPPING)
    closes = {
        "AAA": _close_df([("2024-03-29", 100), ("2024-12-31", 100)]),  # implied 100, actual 100 -> clean
        "BBB": _close_df(
            [("2024-03-29", 0.5), ("2024-06-28", 0.5)]
        ),  # implied 50, actual 0.5 -> 100x -> flag
        "CCC": _close_df(
            [("2024-06-28", 5), ("2024-12-31", 5)]
        ),  # implied 100, actual 5 -> 20x split -> no flag
    }
    perf = {p["ticker"]: p for p in deep.attribute_performance(m, closes, None)}
    assert perf["AAA"]["filing_px_check"]["flag"] is False
    assert perf["BBB"]["filing_px_check"]["flag"] is True
    assert perf["CCC"]["filing_px_check"]["flag"] is False


def _scale_filing(specs):
    # specs: list of (cusip, value, shares)
    return Filing("9", "a", P1, "2024-05-15", [_pos(c, c, v, s) for c, v, s in specs])


def test_assess_filing_corrects_uniform_10x():
    # every name's implied price is a clean 10x the actual close (tight cluster) -> rescale /10
    f = _scale_filing(
        [
            ("A", 10_000_000, 10_000),
            ("B", 6_000_000, 10_000),
            ("C", 2_000_000, 10_000),
            ("D", 50_000_000, 100_000),
            ("E", 30_000_000, 100_000),
            ("F", 9_000_000, 30_000),
        ]
    )
    closes = {
        tk: _close_df([("2024-03-29", px)])
        for tk, px in (("A", 100), ("B", 60), ("C", 20), ("D", 50), ("E", 30), ("F", 30))
    }
    mp = {c: c for c in "ABCDEF"}
    cf, factor, status = deep.assess_filing(f, closes, mp)
    assert (factor, status) == (10, "corrected")
    vals = {p.cusip: p.value for p in cf.positions}
    assert vals["A"] == 1_000_000 and vals["D"] == 5_000_000  # values /10
    assert cf.positions[0].shares == 10_000  # shares untouched


def test_assess_filing_ignores_split_outlier_when_clean():
    # most names ratio ~1, two split names ~20x -> consistency high, median ~1 -> untouched/ok
    f = _scale_filing(
        [
            ("A", 1_000_000, 10_000),
            ("B", 600_000, 10_000),
            ("C", 200_000, 10_000),
            ("D", 500_000, 10_000),
            ("E", 300_000, 10_000),
            ("S1", 3_000_000, 1_000),
            ("S2", 4_000_000, 1_000),
        ]
    )
    closes = {
        tk: _close_df([("2024-03-29", px)])
        for tk, px in (("A", 100), ("B", 60), ("C", 20), ("D", 50), ("E", 30), ("S1", 150), ("S2", 200))
    }
    mp = {c: c for c in ["A", "B", "C", "D", "E", "S1", "S2"]}
    cf, factor, status = deep.assess_filing(f, closes, mp)
    assert (factor, status) == (1, "ok")  # split outliers ignored, scale is sane
    assert cf is f


def test_assess_filing_flags_garbled_as_anomalous():
    # scattered ratios (no single factor explains it) + median far from 1 -> anomalous, NOT corrected
    f = _scale_filing(
        [
            ("A", 100_000_000, 40_000_000),
            ("B", 250_000_000, 45_000_000),
            ("C", 250_000_000, 23_000_000),
            ("D", 200_000_000, 1_500_000),
            ("E", 2_500_000_000, 50_000_000),
            ("F", 150_000_000, 15_000_000),
        ]
    )
    closes = {
        tk: _close_df([("2024-03-29", px)])
        for tk, px in (("A", 420), ("B", 180), ("C", 94), ("D", 7.8), ("E", 20), ("F", 105))
    }
    mp = {c: c for c in "ABCDEF"}
    cf, factor, status = deep.assess_filing(f, closes, mp)
    assert (factor, status) == (1, "anomalous")  # don't guess a wrong number
    assert cf is f


def test_matrix_weights_use_book_pct_not_sleeve():
    # regression: a tiny $5k CALL is 100% of the (tiny) call sleeve but ~0% of the BOOK.
    # The matrix mixes sleeves, so it must weight by gross book, never sleeve-relative.
    big = _pos("BIG CORP", "BIG", 100_000_000, 1_000_000)
    call = Position(
        name="BIG CORP",
        cusip="BIG",
        title_class="COM",
        value=5_000,
        shares=10_000,
        sh_type="SH",
        put_call="CALL",
    )
    m = deep.build_matrix([Filing("9", "a", P1, "2024-05-15", [big, call])], {"BIG": "BIG"})
    by_side = {(r["cusip"], r["side"]): r for r in m["rows"]}
    assert by_side[("BIG", "LONG")]["peak_weight"] > 99  # ~the whole book
    assert by_side[("BIG", "CALL")]["peak_weight"] < 1  # NOT 100% — a rounding speck of the book


def test_assess_filing_ignores_prn_debt_rows():
    # PRN/debt rows have value/shares != share price; they must NOT drive a (false) rescale
    prn = [
        Position(
            name=f"BOND{i}",
            cusip=f"B{i}",
            title_class="NOTE",
            value=100_000_000,
            shares=100_000_000,
            sh_type="PRN",
            put_call="",
        )
        for i in range(6)
    ]
    f = Filing("9", "a", P1, "2024-05-15", prn)  # implied 1.0 vs close 100 -> ratio 0.01 IF counted
    closes = {f"B{i}": _close_df([("2024-03-29", 100)]) for i in range(6)}
    mp = {f"B{i}": f"B{i}" for i in range(6)}
    cf, factor, status = deep.assess_filing(f, closes, mp)
    assert (factor, status) == (1, "ok")  # PRN excluded from the sample -> nothing to judge -> trusted
    assert cf is f


def test_still_held_alpha_uses_matched_end_date():
    # security history ends 2024-09-30; benchmark runs to 2024-12-31 -> alpha must use the SAME end date
    m = deep.build_matrix(
        [Filing("9", "a", P1, "2024-05-15", [_pos("AAA", "AAA", 10_000_000, 100_000)])], {"AAA": "AAA"}
    )
    closes = {"AAA": _close_df([("2024-03-29", 100), ("2024-09-30", 150)])}  # ends 2024-09-30
    bench = _close_df([("2024-03-29", 400), ("2024-09-30", 420), ("2024-12-31", 480)])  # runs later
    a = {p["ticker"]: p for p in deep.attribute_performance(m, closes, bench)}["AAA"]
    assert a["still_held"]
    assert round(a["holding_return_pct"], 1) == 50.0  # 100 -> 150
    assert round(a["bench_return_pct"], 1) == 5.0  # 400 -> 420 (to 2024-09-30), NOT 480
    assert a["window_end"] == "2024-09-30"


def test_contiguous_gaps_and_contribution():
    # DDD held Q1 and Q3 but NOT Q2 -> non-contiguous, 1 gap; contribution = avg book-weight x return
    q1 = Filing("9", "a", P1, "x", [_pos("DDD CORP", "DDD", 10_000_000, 100_000)])
    q2 = Filing("9", "b", P2, "x", [_pos("OTHER INC", "OTH", 5_000_000, 100_000)])
    q3 = Filing("9", "c", P3, "x", [_pos("DDD CORP", "DDD", 10_000_000, 100_000)])
    m = deep.build_matrix([q1, q2, q3], {"DDD": "DDD", "OTH": "OTH"})
    ddd = next(r for r in m["rows"] if r["ticker"] == "DDD")
    assert ddd["n_quarters_held"] == 2 and ddd["contiguous"] is False and ddd["n_gaps"] == 1
    assert ddd["avg_held_weight"] > 0
    oth = next(r for r in m["rows"] if r["ticker"] == "OTH")
    assert oth["contiguous"] is True and oth["n_gaps"] == 0
    # contribution flows through performance
    closes = {
        "DDD": _close_df([("2024-03-29", 100), ("2024-09-30", 200), ("2024-12-31", 200)]),
        "OTH": _close_df([("2024-06-28", 50), ("2024-12-31", 50)]),
    }
    perf = {p["ticker"]: p for p in deep.attribute_performance(m, closes, None)}
    p = perf["DDD"]  # held -> entry 100 -> now 200 = +100%
    assert round(p["holding_return_pct"], 0) == 100.0
    assert round(p["contrib_hold"], 2) == round(p["avg_held_weight"] * 1.0, 2)


def test_filer_name_mismatch():
    assert deep.filer_name_mismatch("SUNCOR ENERGY ORD", "Thermo Fisher Scientific Inc.") is True
    assert deep.filer_name_mismatch("EATON CORP PLC", "Eaton Corporation, PLC") is False
    assert (
        deep.filer_name_mismatch("TAIWAN SEMICONDUCTOR MANUFAC", "Taiwan Semiconductor Manufacturing")
        is False
    )
    assert (
        deep.filer_name_mismatch("FREEPORT MCMORAN INC", "Freeport-McMoRan Inc.") is False
    )  # hyphen tolerated? no shared token-> check
    assert (
        deep.filer_name_mismatch("OSISKO GOLD ROYALTIES LTD", "OR Royalties Inc.") is False
    )  # share "ROYALTIES"
    assert deep.filer_name_mismatch("", "Anything") is False


def test_flow_forward_returns():
    m = deep.build_matrix(_history(), MAPPING)
    flows = deep.flow_ledger(_history(), MAPPING)
    closes = {
        "AAA": _close_df([("2024-06-28", 120), ("2024-09-30", 150), ("2024-12-31", 180)]),
        "CCC": _close_df([("2024-06-28", 100), ("2024-09-30", 90), ("2024-12-31", 95)]),
        "BBB": _close_df([("2024-03-29", 50), ("2024-06-28", 60)]),
    }
    deep.attach_flow_forward(flows, closes, m["periods"])
    aaa_add = next(e for e in flows if e["ticker"] == "AAA" and e["action"] == "Add")  # Q2 -> Q3
    assert round(aaa_add["fwd_1q_pct"], 1) == 25.0  # 120 -> 150
