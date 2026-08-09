"""End-to-end orchestration: manager name/CIK -> enriched, analyzed data dict.

This is the reusable core every output (xlsx, png, future Telegram push) consumes.
Deterministic except for the live data fetch; the analytics layer is pure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import analytics, edgar, enrich, resolve

OUTPUT = Path(__file__).resolve().parent.parent / "output"


def run(name_or_cik: str, *, benchmark: str = "SPY", save: bool = True) -> dict:
    cik10, official, latest, prior = edgar.latest_and_prior(name_or_cik)
    rows, sl, sl_prev = analytics.enrich_rows(latest, prior)

    cusips = [r["cusip"] for r in rows] + [e["cusip"] for e in analytics.exits(latest, prior)]
    mapping, unresolved = resolve.resolve(cusips)
    for r in rows:
        r["ticker"] = mapping.get(r["cusip"])

    tickers = [r["ticker"] for r in rows if r["ticker"]]
    metrics, errors = enrich.enrich_tickers(tickers)
    bench_ytd = enrich.benchmark_ytd(benchmark)

    for r in rows:
        m = metrics.get(r["ticker"], {})
        r.update(
            {
                k: m.get(k)
                for k in (
                    "price",
                    "ma200",
                    "pct_vs_ma200",
                    "pct_from_hi",
                    "rsi14",
                    "atr_pct",
                    "ytd",
                    "r3m",
                    "trend",
                    "rating",
                    "rec_mean",
                    "n_analysts",
                    "target_mean",
                    "upside",
                    "mktcap",
                    "pe",
                    "fpe",
                    "beta",
                    "sector",
                    "industry",
                )
            }
        )
        # mark-to-market vs the 13F implied price (long & options alike)
        r["post_filing_pct"] = (
            (r["price"] / r["filing_px"] - 1) * 100 if r.get("price") and r.get("filing_px") else None
        )
        r["rs_vs_bench"] = (r["ytd"] - bench_ytd) if r.get("ytd") is not None else None

    # exits, enriched with since-exit move
    ex = analytics.exits(latest, prior)
    for e in ex:
        e["ticker"] = mapping.get(e["cusip"])
        m = metrics.get(e["ticker"], {})
        e["price"] = m.get("price")
        e["sector"] = m.get("sector")
        # A roll (same name, new side) isn't a real exit -> no meaningful since-exit move.
        # (A clean option exit IS fine: 13F option value = underlying notional, so
        # exit_px = value/shares ≈ the underlying price.)
        if e.get("roll") or not (e.get("price") and e.get("exit_px")):
            e["since_exit_pct"] = None
        else:
            pct = (e["price"] / e["exit_px"] - 1) * 100
            e["since_exit_pct"] = pct if abs(pct) < 1000 else None  # guard split/units artifacts

    sector_of = {r["ticker"]: r.get("sector") for r in rows if r.get("ticker")}
    long_vals = [r["value"] for r in rows if r["side"] == "LONG"]

    data = {
        "meta": {
            "manager": official,
            "cik": cik10,
            "latest_period": latest.period,
            "latest_filed": latest.filed,
            "latest_accession": latest.accession,
            "prior_period": prior.period if prior else None,
            "benchmark": benchmark,
            "benchmark_ytd": bench_ytd,
            "is_options_book": analytics.has_options(latest.positions),
            "n_positions": len(latest.positions),
            "n_positions_prior": len(prior.positions) if prior else 0,
            "sleeves": {
                "long": sl.long,
                "call": sl.call,
                "put": sl.put,
                "gross": sl.gross,
                "net_long": sl.net_long,
            },
            "sleeves_prior": {
                "long": sl_prev.long,
                "call": sl_prev.call,
                "put": sl_prev.put,
                "gross": sl_prev.gross,
            },
            "pareto_long": analytics.pareto(long_vals),
            "unresolved_cusips": unresolved,
            "enrich_errors": errors,
            "generated": datetime.now(UTC).isoformat(),
        },
        "holdings": rows,
        "exits": ex,
        "sectors": analytics.sector_breakdown(rows, sector_of),
    }
    if save:
        OUTPUT.mkdir(exist_ok=True)
        slug = "".join(c if c.isalnum() else "_" for c in official.lower())[:40]
        path = OUTPUT / f"{slug}_{latest.period}.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        data["meta"]["_json_path"] = str(path)
    return data
