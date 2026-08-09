"""Deep mode: multi-quarter 13F EVOLUTION for one manager.

Where the one-quarter engine (`pipeline.run`) answers *"what does the book look like
now?"*, deep mode answers *"how did this manager's mind move over years?"* — the
quarter-by-quarter position matrix, the New/Add/Trim/Exit flow ledger, each position's
lifecycle arc, and a per-bet performance attribution (entry quarter-end -> reported-held
window or now, vs the benchmark over the same window).

Two layers, kept apart so the transforms stay replay-exact:
  * **pure** (`assess_filing`, `build_matrix`, `quarter_summaries`, `flow_ledger`,
    `attribute_performance`, `attach_flow_forward`) — I/O-free, deterministic, unit-tested.
  * **orchestration** (`run_deep`) — the only network-touching function: pulls every
    13F-HR for the CIK (EDGAR-polite), resolves the union universe once, pulls daily
    history once per ticker, then runs the pure transforms and dumps the data dict.

Honest framing baked into every consumer: a 13F is a 45-day-lagged quarter-END snapshot.
We never see intra-quarter trades or his actual fills; marks are quarter-end closes, not
cost basis. Performance is the price path over the *reported-held* window, clearly labelled.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import analytics, edgar, enrich, resolve

OUTPUT = Path(__file__).resolve().parent.parent / "output"
SIDE_ORDER = {"LONG": 2, "CALL": 1, "PUT": 0}
# A genuine unit/CUSIP error makes implied (value/shares) vs actual close differ by orders of
# magnitude; a stock split (yfinance Close is split-adjusted) only ~2-20x. Flag beyond a split's reach.
FILING_PX_FLAG_RATIO = 50


# ----------------------------------------------------------------------------- pure transforms


def _row_key(cusip: str, put_call: str) -> str:
    return f"{cusip}|{put_call}" if put_call else cusip


_NAME_STOP = {
    "INC",
    "CORP",
    "CO",
    "LTD",
    "PLC",
    "THE",
    "GROUP",
    "HLDGS",
    "HOLDINGS",
    "NEW",
    "ORD",
    "SA",
    "NV",
    "COM",
    "CL",
    "SHS",
    "ADR",
    "ADS",
}


def filer_name_mismatch(filing_name: str, resolved_name: str) -> bool:
    """True if the filer's nameOfIssuer shares NO significant word with the resolved ticker's real
    company name — i.e. the filer mislabeled the line (the CUSIP+price are authoritative). E.g. a
    Thermo Fisher CUSIP filed as 'SUNCOR ENERGY ORD'. Tolerant of suffix/punctuation differences."""

    def toks(s):
        for ch in ",-/.":  # split on punctuation so "Freeport-McMoRan" == "FREEPORT MCMORAN"
            s = s.replace(ch, " ")
        return {w for w in s.upper().split() if w not in _NAME_STOP and len(w) > 2}

    if not (filing_name and resolved_name):
        return False
    fw, rw = toks(filing_name), toks(resolved_name)
    return bool(fw and rw and not (fw & rw))


SCALE_CONSISTENCY_MIN = 0.7  # min fraction of names sharing one scale factor to call it a clean unit error


def assess_filing(filing, closes: dict, mapping: dict) -> tuple:
    """Classify a filing's value scale vs reality and correct/flag it. Returns (filing, factor, status).

    Each name's implied price (value/shares) is compared to its actual quarter-end close. Two
    distinct pathologies a multi-year 13F history hits:
      * UNIFORM unit error — EVERY name off by the SAME clean power of ten (a filer reported the
        whole table in the wrong $ unit). Tight cluster around 10^k -> we rescale (status "corrected").
      * GARBLED filing — per-name values/shares internally inconsistent (scattered ratios, the
        median far from 1 with low agreement). Not recoverable by any single factor -> we DON'T
        guess; status "anomalous" so the caller excludes it (honest over a wrong number).
    Median + a consistency fraction separate the two; weights are scale-invariant regardless.
    "ok" = scale looks sane, untouched. factor==1 unless corrected.
    """
    ratios = []
    for p in filing.positions:
        # ONLY true share-equity rows have value/shares == share price. A PRN/debt/convertible row
        # (sh_type != "SH") has value=principal, shares=par amount -> its "implied price" is garbage
        # and would poison the scale judgment. Exclude them from the sample.
        if p.sh_type != "SH":
            continue
        df = closes.get(mapping.get(p.cusip))
        if not p.shares or df is None or df.empty:
            continue
        actual = _close_on(df, filing.period, col="Close")
        if actual and actual > 0:
            ratios.append((p.value / p.shares) / actual)
    n = len(ratios)
    if n < 3:  # too little evidence to judge anything -> trust as filed
        return filing, 1, "ok"
    med = statistics.median(ratios)
    if med <= 0:
        return filing, 1, "ok"
    consistency = sum(1 for r in ratios if 0.5 * med <= r <= 2 * med) / n
    k = round(math.log10(med))
    clean_pow10 = k != 0 and 0.5 <= med / (10**k) <= 2
    # CORRECT a clean power-of-ten unit error; on a thin sample (<5) demand near-perfect agreement.
    if clean_pow10 and consistency >= (0.95 if n < 5 else SCALE_CONSISTENCY_MIN):
        factor = 10**k  # uniform unit error -> rescale every value by 10^-k
        fixed = [replace(p, value=round(p.value / factor)) for p in filing.positions]
        return replace(filing, positions=fixed), factor, "corrected"
    # EXCLUDE a garbled filing only with enough evidence — never drop a quarter on a thin sample.
    if n >= 5 and (med > 3 or med < 1 / 3 or consistency < 0.5):
        return filing, 1, "anomalous"
    return filing, 1, "ok"


def build_matrix(filings: list, mapping: dict[str, str]) -> dict:
    """Position evolution grid. `filings` oldest-first. Returns {periods, rows}.

    Each row is one (cusip, side) sleeve tracked across every quarter it appears, with the
    quarter's action classified against the immediately prior filing (so an "Add" means he
    grew it that quarter). The first filing's positions are all "New" (the book as first seen).
    """
    periods = [f.period for f in filings]
    cells: dict[str, dict] = {}
    meta: dict[str, dict] = {}
    prev_by_key: dict = {}
    for f in filings:
        sl = analytics.sleeves(f.positions)
        denom = {"": sl.long or 1, "CALL": sl.call or 1, "PUT": sl.put or 1}
        gross = sl.gross or 1
        for p in f.positions:
            action, qoq_pct, dshares = analytics.classify_qoq(p, prev_by_key)
            k = _row_key(p.cusip, p.put_call)
            cells.setdefault(k, {})[f.period] = {
                "shares": p.shares,
                "value": p.value,
                "weight": p.value / denom[p.put_call] * 100,
                "book_pct": p.value / gross * 100,
                "action": action,
                "qoq_shares": qoq_pct,
                "delta_shares": dshares,
            }
            # latest-seen identity wins (issuer names drift, e.g. "SKEENA RES LTD" -> "...NEW")
            meta[k] = {
                "cusip": p.cusip,
                "side": p.put_call or "LONG",
                "name": p.name,
                "ticker": mapping.get(p.cusip),
            }
        prev_by_key = {p.key: p for p in f.positions}

    idx_of = {pr: i for i, pr in enumerate(periods)}
    rows = []
    for k, c in cells.items():
        held = [pr for pr in periods if pr in c]
        # book_pct (% of GROSS book), not sleeve_pct: a $5k call is 100% of a $5k call sleeve but
        # ~0% of the book — the matrix mixes sleeves, so it must use the one comparable denominator.
        weights = {pr: c[pr]["book_pct"] for pr in held}
        peak_period = max(weights, key=weights.get)
        span = idx_of[held[-1]] - idx_of[held[0]] + 1  # quarters between first and last appearance
        rows.append(
            {
                **meta[k],
                "cells": c,
                "first_seen": held[0],
                "last_seen": held[-1],
                "still_held": held[-1] == periods[-1],
                "n_quarters_held": len(held),
                "contiguous": span == len(held),  # held every quarter between first and last?
                "n_gaps": span - len(held),  # quarters inside the span the manager was OUT (over the kept timeline)
                "avg_held_weight": sum(weights.values())
                / len(held),  # mean book_pct while held (for contribution)
                "peak_weight": weights[peak_period],
                "peak_period": peak_period,
                "latest_weight": c[periods[-1]]["book_pct"] if periods[-1] in c else None,
                "max_value": max(c[pr]["value"] for pr in held),
            }
        )
    # sort: currently-held first (by latest weight), then former holdings (by peak weight)
    rows.sort(key=lambda r: (r["still_held"], r["latest_weight"] or 0, r["peak_weight"]), reverse=True)
    return {"periods": periods, "rows": rows}


def quarter_summaries(filings: list) -> list[dict]:
    """Per-quarter book shape + flow counts + gross buy/sell estimate. Oldest-first."""
    out = []
    prev = None
    for f in filings:
        sl = analytics.sleeves(f.positions)
        long_vals = [p.value for p in f.positions if not p.put_call]
        prev_by_key = {p.key: p for p in prev.positions} if prev else {}
        counts = {"New": 0, "Add": 0, "Trim": 0, "Hold": 0}
        buy = sell = 0
        for p in f.positions:
            action, _pct, dshares = analytics.classify_qoq(p, prev_by_key)
            counts[action] = counts.get(action, 0) + 1
            px = (p.value / p.shares) if p.shares else 0
            if action == "New":
                buy += p.value
            elif action == "Add":
                buy += abs(dshares) * px
            elif action == "Trim":
                sell += abs(dshares) * px
        ex = analytics.exits(f, prev) if prev else []
        for e in ex:
            sell += e["value"]
        top = sorted(
            (
                {
                    "name": p.name,
                    "cusip": p.cusip,
                    "weight": p.value / (sl.long or 1) * 100,
                    "value": p.value,
                }
                for p in f.positions
                if not p.put_call
            ),
            key=lambda d: -d["weight"],
        )[:10]
        out.append(
            {
                "period": f.period,
                "filed": f.filed,
                "total_value": f.total_value,
                "n_positions": len(f.positions),
                "sleeves": {"long": sl.long, "call": sl.call, "put": sl.put, "gross": sl.gross},
                "pareto": analytics.pareto(long_vals),
                "n_new": counts["New"],
                "n_add": counts["Add"],
                "n_trim": counts["Trim"],
                "n_hold": counts["Hold"],
                "n_exit": len(ex),
                "buy_est": buy,
                "sell_est": sell,
                "top": top,
            }
        )
        prev = f
    return out


def flow_ledger(filings: list, mapping: dict[str, str]) -> list[dict]:
    """Chronological New/Add/Trim/Exit events across consecutive quarters."""
    out = []
    prev = None
    for f in filings:
        prev_by_key = {p.key: p for p in prev.positions} if prev else {}
        sl = analytics.sleeves(f.positions)
        for p in f.positions:
            action, qoq_pct, dshares = analytics.classify_qoq(p, prev_by_key)
            if action == "Hold":
                continue
            out.append(
                {
                    "period": f.period,
                    "cusip": p.cusip,
                    "ticker": mapping.get(p.cusip),
                    "name": p.name,
                    "side": p.put_call or "LONG",
                    "action": action,
                    "value": p.value,
                    "weight": p.value / (sl.long or 1) * 100 if not p.put_call else None,
                    "delta_shares": dshares,
                    "qoq_shares": qoq_pct,
                }
            )
        for e in analytics.exits(f, prev) if prev else []:
            out.append(
                {
                    "period": f.period,
                    "cusip": e["cusip"],
                    "ticker": mapping.get(e["cusip"]),
                    "name": e["name"],
                    "side": e["side"],
                    "action": "Roll" if e.get("roll") else "Exit",
                    "value": e["value"],
                    "weight": None,
                    "delta_shares": -e["shares"],
                    "qoq_shares": None,
                }
            )
        prev = f
    return out


def _close_on(df: pd.DataFrame, period: str, col: str = "AdjClose") -> float | None:
    """Last available close at or before the quarter-end date (handles weekend/holiday ends)."""
    if df is None or df.empty:
        return None
    v = df[col].asof(pd.Timestamp(period))
    return float(v) if pd.notna(v) else None


def attribute_performance(matrix: dict, closes: dict, bench: pd.DataFrame | None) -> list[dict]:
    """Per-position bet attribution over the REPORTED-HELD window.

    entry = quarter-end close the position first appears; exit = close at the last quarter it
    was reported (still-held -> latest available close). Holding return is split/dividend
    adjusted (AdjClose); alpha is vs the benchmark over the same window. A filing-implied vs
    actual close mismatch on the entry quarter is surfaced as a data-quality flag (unit/CUSIP guard).
    """
    out = []
    last_period = matrix["periods"][-1]
    for r in matrix["rows"]:
        tk = r["ticker"]
        df = closes.get(tk)
        entry_p, last_p = r["first_seen"], r["last_seen"]
        entry_close = _close_on(df, entry_p)
        # ONE end date for both the security and the benchmark, so alpha compares the SAME window.
        # Still-held -> the security's own last available date (a stale/delisted ticker stops early);
        # the benchmark is then priced to THAT date too (not its own iloc[-1]).
        if r["still_held"]:
            end_date = df.index[-1].strftime("%Y-%m-%d") if (df is not None and not df.empty) else last_period
            exit_label = "now"
        else:
            end_date = last_p
            exit_label = last_p
        exit_close = _close_on(df, end_date)
        window_end = end_date
        hold_ret = (exit_close / entry_close - 1) * 100 if (entry_close and exit_close) else None
        bench_entry = _close_on(bench, entry_p)
        bench_exit = _close_on(bench, end_date)  # SAME end date as the security -> apples-to-apples
        bench_ret = (bench_exit / bench_entry - 1) * 100 if (bench_entry and bench_exit) else None
        # data-quality: 13F implied entry price (value/shares) vs actual raw close that quarter.
        # After per-filing scale correction, only a true unit/CUSIP error survives at >50x
        # (a stock split is <=~20x and is NOT flagged — yfinance Close is split-adjusted).
        cell = r["cells"][entry_p]
        implied = (cell["value"] / cell["shares"]) if cell["shares"] else None
        actual_raw = _close_on(df, entry_p, col="Close")
        ratio = (implied / actual_raw) if (implied and actual_raw) else None
        diverge = abs(ratio - 1) if ratio is not None else None
        px_flag = bool(ratio is not None and not (1 / FILING_PX_FLAG_RATIO <= ratio <= FILING_PX_FLAG_RATIO))
        alpha = (hold_ret - bench_ret) if (hold_ret is not None and bench_ret is not None) else None
        avg_wt = r["avg_held_weight"]
        out.append(
            {
                "ticker": tk,
                "name": r["name"],
                "side": r["side"],
                "still_held": r["still_held"],
                "entry_period": entry_p,
                "exit_period": exit_label,
                "window_end": window_end,
                "n_quarters_held": r["n_quarters_held"],
                "contiguous": r["contiguous"],
                "n_gaps": r["n_gaps"],
                "entry_close": entry_close,
                "exit_close": exit_close,
                "holding_return_pct": hold_ret,
                "bench_return_pct": bench_ret,
                "alpha_pct": alpha,
                # contribution = how much the bet MOVED the book: avg book-weight while held x return.
                # For a concentrated manager this re-ranks "what mattered" away from raw % return.
                "avg_held_weight": avg_wt,
                "contrib_hold": (avg_wt * hold_ret / 100) if hold_ret is not None else None,
                "contrib_alpha": (avg_wt * alpha / 100) if alpha is not None else None,
                "peak_weight": r["peak_weight"],
                "peak_period": r["peak_period"],
                "max_value": r["max_value"],
                "latest_weight": r["latest_weight"],
                "filing_px_check": {
                    "implied": implied,
                    "actual": actual_raw,
                    "divergence": diverge,
                    "flag": px_flag,
                },
            }
        )
    return out


def attach_flow_forward(flows: list[dict], closes: dict, periods: list[str]) -> list[dict]:
    """Annotate each flow event with the stock's forward 1-quarter move and move-to-now.

    The timing signal: when the manager initiated/added, did it work next quarter? On trims/exits,
    was a drawdown dodged? Uses adjusted closes; None where price history is missing.
    """
    nxt = {periods[i]: periods[i + 1] for i in range(len(periods) - 1)}
    for ev in flows:
        df = closes.get(ev["ticker"])
        p0 = ev["period"]
        c0 = _close_on(df, p0)
        p1 = nxt.get(p0)
        c1 = _close_on(df, p1) if p1 else None
        c_now = float(df["AdjClose"].iloc[-1]) if (df is not None and not df.empty) else None
        ev["fwd_1q_pct"] = (c1 / c0 - 1) * 100 if (c0 and c1) else None
        ev["fwd_to_now_pct"] = (c_now / c0 - 1) * 100 if (c0 and c_now) else None
    return flows


# ----------------------------------------------------------------------------- orchestration (network)


def run_deep(
    name_or_cik: str, *, benchmark: str = "SPY", quarters: int | None = None, save: bool = True
) -> dict:
    """Pull every 13F-HR (or the most recent `quarters`), build the full evolution data dict."""
    if name_or_cik.isdigit() or name_or_cik.lower().startswith("cik"):
        cik10 = "".join(c for c in name_or_cik if c.isdigit()).zfill(10)
    else:
        cik10, _ = edgar.discover_cik(name_or_cik)
    official, filing_metas = edgar.list_13f(cik10)  # newest-first
    cik_nozero = cik10.lstrip("0")
    if quarters:
        filing_metas = filing_metas[:quarters]
    metas = list(reversed(filing_metas))  # oldest-first for an evolution timeline
    filings = [edgar.fetch_filing(cik_nozero, m) for m in metas]

    # resolve the union of every CUSIP ever held (one compounding pass)
    all_cusips = sorted({p.cusip for f in filings for p in f.positions})
    mapping, unresolved = resolve.resolve(all_cusips)

    # one daily-history pull per ticker (covers the whole window), + the benchmark.
    # Pulled BEFORE the transforms so we can detect/correct per-filing value-scale errors.
    tickers = sorted({t for t in mapping.values() if t})
    start = (pd.Timestamp(filings[0].period) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    closes, price_errors = enrich.historical_closes(tickers, start)
    bench_map, _ = enrich.historical_closes([benchmark], start)
    bench = bench_map.get(benchmark)

    # assess each filing's value scale vs reality: rescale a clean uniform unit error, EXCLUDE a
    # garbled filing (don't guess a wrong number), keep the rest. Weights are scale-invariant; this
    # protects absolute $ (AUM, buy/sell, px) and the flow timeline from a single corrupt filing.
    scale_corrections, excluded_filings, kept = [], [], []
    for f in filings:
        cf, factor, status = assess_filing(f, closes, mapping)
        if status == "corrected":
            scale_corrections.append({"period": f.period, "factor": factor})
            kept.append(cf)
        elif status == "anomalous":
            excluded_filings.append(
                {
                    "period": f.period,
                    "accession": f.accession,
                    "raw_total": f.total_value,
                    "reason": "garbled value/share scale on EDGAR — not reconcilable by a single factor",
                }
            )
        else:
            kept.append(cf)
    filings = kept

    matrix = build_matrix(filings, mapping)

    # enrich ALL resolved tickers (not just current) so EXITED names also carry sector/industry and a
    # canonical company name — needed for full-universe sector analysis and to catch filer name errors.
    # Done BEFORE performance/flows so the corrected display name propagates everywhere.
    all_tickers = sorted({r["ticker"] for r in matrix["rows"] if r["ticker"]})
    metrics, enrich_errors = enrich.enrich_tickers(all_tickers)
    name_mismatches = []
    for r in matrix["rows"]:
        m = metrics.get(r["ticker"], {})
        r["sector"] = m.get("sector")
        r["industry"] = m.get("industry")
        r["resolved_name"] = m.get("resolved_name")
        r["convexity_tag"] = None  # joined from the analytical synthesis at render time
        # filer name-error guard: the CUSIP (authoritative) resolved to a ticker whose real company
        # name shares NO word with the filer's nameOfIssuer -> the filer mislabeled the line. The price
        # already proves identity (Thermo's $667, not Suncor's $35); we surface + correct the DISPLAY name.
        if filer_name_mismatch(r.get("name"), r.get("resolved_name")):
            name_mismatches.append(
                {
                    "cusip": r["cusip"],
                    "ticker": r["ticker"],
                    "filed_as": r["name"],
                    "actually": r["resolved_name"],
                }
            )
            r["filer_name_mismatch"] = True
            r["name"] = f"{r['resolved_name']} (filed as {r['name']})"

    # build the downstream views AFTER the name correction so it propagates to perf + flows + quarter tops
    name_by_cusip = {r["cusip"]: r["name"] for r in matrix["rows"]}
    quarters_summary = quarter_summaries(filings)
    for q in quarters_summary:
        for t in q["top"]:
            t["name"] = name_by_cusip.get(t["cusip"], t["name"])
    flows = flow_ledger(filings, mapping)
    for e in flows:
        e["name"] = name_by_cusip.get(e["cusip"], e["name"])
    performance = attribute_performance(matrix, closes, bench)
    flows = attach_flow_forward(flows, closes, matrix["periods"])

    any_opts = any(p.put_call for f in filings for p in f.positions)
    data = {
        "meta": {
            "manager": official,
            "cik": cik10,
            "benchmark": benchmark,
            "n_quarters": len(filings),
            "first_period": filings[0].period,
            "latest_period": filings[-1].period,
            "is_options_book": any_opts,
            "universe_size": len(all_cusips),
            "unresolved_cusips": unresolved,
            "price_errors": price_errors,
            "enrich_errors": enrich_errors,
            "scale_corrections": scale_corrections,
            "excluded_filings": excluded_filings,
            "name_mismatches": name_mismatches,
            "n_unmarked": sum(1 for p in performance if p["holding_return_pct"] is None),
            "px_quality_flags": [
                {
                    "ticker": p["ticker"],
                    "entry": p["entry_period"],
                    "divergence": p["filing_px_check"]["divergence"],
                }
                for p in performance
                if p["filing_px_check"]["flag"]
            ],
            "generated": datetime.now(UTC).isoformat(),
        },
        "quarters": quarters_summary,
        "matrix": matrix,
        "flows": flows,
        "performance": performance,
    }
    if save:
        OUTPUT.mkdir(exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in official.lower())[:40]
        path = OUTPUT / f"{slug}_deep.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        data["meta"]["_json_path"] = str(path)
    return data
