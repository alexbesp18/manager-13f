"""Pure-analytics tests. No network. Covers the options-aware sleeve logic,
QoQ classification, Pareto, and the fail-loud contract."""

import pytest

from manager13f import analytics
from manager13f.edgar import Filing, Position, _normalize_value_units


def pos(cusip, value, shares, pc="", name=None):
    return Position(
        name=name or cusip,
        cusip=cusip,
        title_class="COM",
        value=value,
        shares=shares,
        sh_type="SH",
        put_call=pc,
    )


def test_sleeves_separate_long_call_put():
    ps = [pos("A", 100, 10), pos("B", 50, 5, "CALL"), pos("C", 300, 30, "PUT")]
    s = analytics.sleeves(ps)
    assert (s.long, s.call, s.put) == (100, 50, 300)
    assert s.gross == 450
    assert s.net_long == 100 + 50 - 300  # net SHORT


def test_same_issuer_three_sides_kept_distinct():
    # An issuer held as common, call, AND put must not collapse into one weight.
    ps = [pos("X", 100, 10), pos("X", 40, 4, "CALL"), pos("X", 200, 20, "PUT")]
    keys = {p.key for p in ps}
    assert len(keys) == 3
    s = analytics.sleeves(ps)
    assert (s.long, s.call, s.put) == (100, 40, 200)


def test_weights_are_within_sleeve_not_gross():
    latest = Filing(
        "1", "a", "2026-03-31", "2026-05-15", [pos("A", 80, 8), pos("B", 20, 2), pos("C", 900, 90, "PUT")]
    )
    rows, sl, _ = analytics.enrich_rows(latest, None)
    a = next(r for r in rows if r["cusip"] == "A")
    assert a["sleeve_pct"] == pytest.approx(80.0)  # 80 of 100 long, NOT 80 of 1000 gross
    put = next(r for r in rows if r["cusip"] == "C")
    assert put["sleeve_pct"] == pytest.approx(100.0)  # the only put = 100% of put sleeve


def test_qoq_classification():
    prior = Filing(
        "1",
        "p",
        "2025-12-31",
        "2026-02-15",
        [pos("HOLD", 100, 100), pos("TRIM", 100, 100), pos("GONE", 50, 50)],
    )
    latest = Filing(
        "1", "a", "2026-03-31", "2026-05-15", [pos("HOLD", 100, 100), pos("TRIM", 60, 60), pos("NEW", 10, 10)]
    )
    pbk = {p.key: p for p in prior.positions}
    assert analytics.classify_qoq(latest.positions[0], pbk)[0] == "Hold"
    assert analytics.classify_qoq(latest.positions[1], pbk)[0] == "Trim"
    assert analytics.classify_qoq(latest.positions[2], pbk)[0] == "New"
    ex = analytics.exits(latest, prior)
    assert [e["cusip"] for e in ex] == ["GONE"]


def test_pareto_80_20():
    p = analytics.pareto([80, 10, 5, 3, 2])  # one name ~80%
    assert p["n80"] == 1
    assert p["top5"] == pytest.approx(100.0)
    assert p["hhi"] > 6000  # very concentrated


def test_pareto_empty_safe():
    p = analytics.pareto([])
    assert p["n"] == 0 and p["hhi"] == 0


def test_roll_long_to_call_flagged_not_a_clean_exit():
    # A common long rolled into a call: the long key is "gone" but the CUSIP persists.
    prior = Filing("1", "p", "2025-12-31", "2026-02-15", [pos("RSP", 100, 50)])
    latest = Filing("1", "a", "2026-03-31", "2026-05-15", [pos("RSP", 80, 40, "CALL")])
    ex = analytics.exits(latest, prior)
    assert len(ex) == 1 and ex[0]["cusip"] == "RSP"
    assert ex[0]["roll"] is True  # so the caller nulls a bogus since-exit move

    # A genuine exit (CUSIP truly gone) is not a roll.
    latest2 = Filing("1", "a", "2026-03-31", "2026-05-15", [pos("XYZ", 10, 5)])
    ex2 = analytics.exits(latest2, prior)
    assert ex2[0]["cusip"] == "RSP" and ex2[0]["roll"] is False


def test_grown_from_token_placeholder_reads_as_new_not_add():
    # Prior was a 1-share $37 placeholder; now a real $8.9M long -> "New", not "Add +20,000,000%".
    prior = Filing("1", "p", "2025-12-31", "2026-02-15", [pos("INTC", 37, 1)])
    latest = Filing("1", "a", "2026-03-31", "2026-05-15", [pos("INTC", 8_929_441, 202_344)])
    pbk = {p.key: p for p in prior.positions}
    action, qoq, _ = analytics.classify_qoq(latest.positions[0], pbk)
    assert action == "New" and qoq is None
    # A real add off a real base stays "Add".
    prior2 = {pos("X", 5_000_000, 50_000).key: pos("X", 5_000_000, 50_000)}
    assert analytics.classify_qoq(pos("X", 7_500_000, 75_000), prior2)[0] == "Add"


def test_13f_thousand_dollar_values_normalized_to_whole_usd():
    positions = [pos("NTRA", 612_691, 3_063_606), pos("INSM", 188_717, 1_154_090)]

    _normalize_value_units(positions)

    assert positions[0].value == 612_691_000
    assert positions[0].value / positions[0].shares == pytest.approx(199.9901423355353)
