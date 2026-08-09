"""Regression tests for 13F unit-scale inference (the silent x1000 fix).

The old normalizer multiplied EVERY value by 1000 whenever the parsed total was
< $100M, with no log and no guard — which would inflate a legitimately small
whole-dollar book 1000x (the inverse of the bug it fixed). Scale is now inferred
from the median implied price of common-stock holdings, with the AUM floor kept
only as a fallback for filings with no common-stock signal."""

from manager13f.edgar import Position, _infer_scale, _normalize_value_units


def _p(value, shares, pc="", sh_type="SH", cusip="AAA"):
    return Position(
        name=cusip,
        cusip=cusip,
        title_class="COM",
        value=value,
        shares=shares,
        sh_type=sh_type,
        put_call=pc,
    )


def test_infer_scale_whole_dollars():
    positions = [_p(150_000_000, 1_000_000, cusip="A"), _p(50_000_000, 250_000, cusip="B")]
    assert _infer_scale(positions) == 1


def test_infer_scale_thousands():
    positions = [_p(150_000, 1_000_000, cusip="A"), _p(50_000, 250_000, cusip="B")]
    assert _infer_scale(positions) == 1000


def test_small_whole_dollar_book_not_inflated():
    # The bug: a genuinely small (~$50M) book reported in WHOLE dollars. The old
    # proxy (total < $100M -> x1000) would 1000x it; implied prices are real
    # ($200/sh > $1) so the new inference correctly leaves it alone.
    positions = [_p(30_000_000, 150_000, cusip="A"), _p(20_000_000, 100_000, cusip="B")]
    assert sum(p.value for p in positions) < 100_000_000  # trips the old floor
    _normalize_value_units(positions)
    assert positions[0].value == 30_000_000  # unchanged — NOT x1000'd
    assert positions[1].value == 20_000_000


def test_thousands_book_still_normalized():
    positions = [_p(612_691, 3_063_606, cusip="NTRA")]  # implied 0.2 -> $000
    _normalize_value_units(positions)
    assert positions[0].value == 612_691_000  # correctly x1000'd to whole USD


def test_no_common_stock_signal_falls_back_to_floor():
    # All-options filing (no SH equity signal) -> fall back to the AUM floor,
    # without crashing.
    options = [_p(500_000, 10_000, pc="CALL", cusip="A")]
    assert _infer_scale(options) in (1, 1000)
