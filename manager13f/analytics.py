"""Pure, I/O-free, replay-exact analytics over parsed 13F filings.

Option-aware. A 13F mixes three exposure sleeves that must NOT be summed naively:
  - LONG  : common/ADR/ETF held outright (bullish, real capital)
  - CALL  : long-call overlays (bullish leverage; value = underlying notional)
  - PUT   : long-put overlays (bearish/hedge; value = underlying notional)
"% of book" weights are computed within the LONG sleeve only, so a big put line
never masquerades as a big long. Net directional tilt = (LONG + CALL) vs PUT.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Sleeves:
    long: int
    call: int
    put: int

    @property
    def gross(self) -> int:
        return self.long + self.call + self.put

    @property
    def net_long(self) -> int:
        return self.long + self.call - self.put


def sleeves(positions) -> Sleeves:
    s = Sleeves(0, 0, 0)
    for p in positions:
        if p.put_call == "PUT":
            s.put += p.value
        elif p.put_call == "CALL":
            s.call += p.value
        else:
            s.long += p.value
    return s


def has_options(positions) -> bool:
    return any(p.put_call for p in positions)


TOKEN_VALUE = 10_000  # a prior position below this (e.g. a 1-share $37 placeholder) is not a real position


def classify_qoq(cur, prev_by_key: dict) -> tuple[str, float | None, int]:
    """Return (action, qoq_shares_pct, delta_shares) for a current position."""
    prev = prev_by_key.get(cur.key)
    # No prior, OR the prior was a sub-$10k token/placeholder that became a real position
    # -> treat as initiated ("New"), not an "Add +20,000,000%" off 1 share.
    if prev is None or (prev.value < TOKEN_VALUE <= cur.value):
        return "New", None, cur.shares
    d = cur.shares - prev.shares
    pct = (cur.shares / prev.shares - 1) * 100 if prev.shares else None
    if pct is None or abs(pct) < 1:
        return "Hold", pct, d
    return ("Add" if d > 0 else "Trim"), pct, d


def enrich_rows(latest, prior):
    """Build per-position rows with weights (within sleeve), QoQ, and filing price.
    Returns (rows, sleeves_latest, sleeves_prior)."""
    sl = sleeves(latest.positions)
    sl_prev = sleeves(prior.positions) if prior else Sleeves(0, 0, 0)
    prev_by_key = {p.key: p for p in (prior.positions if prior else [])}
    denom = {"": sl.long or 1, "CALL": sl.call or 1, "PUT": sl.put or 1}
    rows = []
    for p in latest.positions:
        action, qoq_pct, dshares = classify_qoq(p, prev_by_key)
        rows.append(
            {
                "name": p.name,
                "cusip": p.cusip,
                "class": p.title_class,
                "side": p.put_call or "LONG",
                "value": p.value,
                "shares": p.shares,
                "sleeve_pct": p.value / denom[p.put_call] * 100,
                "book_pct": p.value / (sl.gross or 1) * 100,
                "action": action,
                "qoq_shares": qoq_pct,
                "delta_shares": dshares,
                "filing_px": (p.value / p.shares) if p.shares else None,
            }
        )
    rows.sort(key=lambda r: (-{"LONG": 2, "CALL": 1, "PUT": 0}[r["side"]], -r["value"]))
    return rows, sl, sl_prev


def pareto(values: list[float]) -> dict:
    """Pareto stats over a list of weights (e.g. long sleeve values). 80/20 + concentration."""
    vals = sorted(values, reverse=True)
    total = sum(vals) or 1
    cum = 0.0
    n80 = n50 = len(vals)
    for i, v in enumerate(vals):
        cum += v
        if cum / total >= 0.5 and n50 == len(vals):
            n50 = i + 1
        if cum / total >= 0.8:
            n80 = i + 1
            break
    weights = [v / total * 100 for v in vals]
    hhi = sum(w**2 for w in weights)
    return {
        "n": len(vals),
        "n80": n80,
        "n50": n50,
        "top5": sum(weights[:5]),
        "top10": sum(weights[:10]),
        "hhi": hhi,
        "effective_n": (10000 / hhi) if hhi else 0,
    }


def exits(latest, prior) -> list[dict]:
    """Positions in prior but gone in latest (by key).

    A position whose CUSIP still appears in the latest filing under a DIFFERENT side
    (e.g. a common long rolled into a call) is a ROLL, not a true exit — flagged so the
    caller doesn't compute a meaningless since-exit move on it.
    """
    cur_keys = {p.key for p in latest.positions}
    cur_cusips = {p.cusip for p in latest.positions}
    out = []
    for p in prior.positions if prior else []:
        if p.key not in cur_keys:
            out.append(
                {
                    "name": p.name,
                    "cusip": p.cusip,
                    "side": p.put_call or "LONG",
                    "value": p.value,
                    "shares": p.shares,
                    "exit_px": (p.value / p.shares) if p.shares else None,
                    "roll": p.cusip in cur_cusips,  # same name still held, different side
                }
            )
    out.sort(key=lambda r: -r["value"])
    return out


def sector_breakdown(rows, sector_of: dict) -> list[tuple[str, float]]:
    """Long-sleeve weight by sector. sector_of maps ticker/cusip -> sector."""
    agg: dict[str, float] = {}
    for r in rows:
        if r["side"] != "LONG":
            continue
        s = sector_of.get(r.get("ticker") or r["cusip"]) or "Other"
        agg[s] = agg.get(s, 0) + r["sleeve_pct"]
    return sorted(agg.items(), key=lambda x: -x[1])
