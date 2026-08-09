"""Phone-glanceable PNG/PDF dashboard preview from the pipeline data dict.
Adaptive: options books show LONG/CALL/PUT sleeves + a net-exposure bar; long-only
shows the single holdings table. Independent of the xlsx (matplotlib)."""

from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

plt.rcParams["text.parse_math"] = False  # treat '$' literally (dollar amounts, not LaTeX math)

NAVY, NAVY2, BRONZE, GOLD = "#1B2A4A", "#26395E", "#B0763A", "#C9A227"
LT, LT2, GREEN, RED, GREY, INK = "#F4F1EA", "#EAE4D6", "#1E7F4F", "#B3261E", "#6B6B6B", "#1B1B1B"
TEAL = "#0B6E8F"


def _pc(v, d=0):
    return "" if v is None else f"{v:+.{d}f}%"


def _gr(v):
    return GREEN if (v or 0) > 0 else (RED if (v or 0) < 0 else GREY)


def render(data: dict, out_png: str, synthesis: dict | None = None) -> str:
    M, H = data["meta"], data["holdings"]
    syn = (synthesis or {}).get("synthesis", {})
    sl = M["sleeves"]
    opts = M["is_options_book"]

    fig = plt.figure(figsize=(15.5, 13.0), dpi=130)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.add_patch(Rectangle((0, 95.8), 100, 4.2, color=NAVY))
    ax.text(
        1,
        97.8,
        f"{M['manager'].upper()}  —  13F INTELLIGENCE",
        color=GOLD,
        fontsize=19,
        fontweight="bold",
        va="center",
    )
    ax.add_patch(Rectangle((0, 93.6), 100, 2.2, color=NAVY2))
    book = "LONG/SHORT OPTIONS BOOK" if opts else "LONG EQUITY BOOK"
    ax.text(
        1,
        94.7,
        f"{book} · {M['latest_period']} (filed {M['latest_filed']}) · CIK {M['cik']} · SEC EDGAR + yfinance",
        color=LT,
        fontsize=9.5,
        va="center",
    )

    # KPI strip
    if opts:
        kpis = [
            ("GROSS 13F", f"${sl['gross'] / 1e9:.1f}B", GOLD),
            ("LONG", f"${sl['long'] / 1e9:.2f}B", "#9AE6B4"),
            ("CALL", f"${sl['call'] / 1e9:.2f}B", "#90CDF4"),
            ("PUT", f"${sl['put'] / 1e9:.2f}B", "#FEB2B2"),
            ("NET (L+C−P)", f"${sl['net_long'] / 1e9:+.2f}B", "#FEB2B2" if sl["net_long"] < 0 else "#9AE6B4"),
            ("POSITIONS", f"{M['n_positions']}", "white"),
            (f"{M['benchmark']} YTD", f"+{M['benchmark_ytd']:.0f}%", LT2),
        ]
    else:
        p = M["pareto_long"]
        kpis = [
            (
                "13F AUM",
                f"${sl['long'] / 1e9:.2f}B" if sl["long"] >= 1e9 else f"${sl['long'] / 1e6:.0f}M",
                GOLD,
            ),
            ("POSITIONS", f"{M['n_positions']}", "white"),
            ("TOP-5 WT", f"{p['top5']:.0f}%", "white"),
            ("HHI", f"{p['hhi']:,.0f}", LT2),
            ("EFF. NAMES", f"{p['effective_n']:.0f}", "white"),
            (f"{M['benchmark']} YTD", f"+{M['benchmark_ytd']:.0f}%", LT2),
        ]
    w = 100 / len(kpis)
    for i, (lab, val, c) in enumerate(kpis):
        x = i * w
        ax.add_patch(Rectangle((x, 88.4), w - 0.3, 4.6, color=NAVY))
        ax.text(x + w / 2, 91.7, lab, color=LT2, fontsize=8, ha="center", fontweight="bold")
        ax.text(x + w / 2, 89.8, val, color=c, fontsize=15, ha="center", fontweight="bold")

    y = 87.4
    if syn.get("theme_headline"):
        ax.add_patch(Rectangle((0, 85.7), 100, 1.7, color=GOLD))
        ax.text(
            0.8,
            86.55,
            "★ " + syn["theme_headline"][:118],
            color=NAVY,
            fontsize=10.5,
            fontweight="bold",
            va="center",
        )
        y = 85.7

    # holdings: sleeves or single
    sides = [("LONG", GREEN), ("CALL", TEAL), ("PUT", RED)] if opts else [("LONG", GREEN)]
    top = y - 0.4
    # cap rows so 3 sleeves + the WOW block fit one canvas
    caps = {"LONG": 10, "CALL": 5, "PUT": 9} if opts else {"LONG": 17}
    rowh = 1.45
    yy = top
    for side, col in sides:
        allrows = [h for h in H if h["side"] == side]
        if not allrows:
            continue
        rows = allrows[: caps.get(side, 12)]
        sval = sum(h["value"] for h in [x for x in H if x["side"] == side])
        ax.add_patch(Rectangle((0, yy - 1.2), 100, 1.2, color=col))
        lab = {
            "LONG": "LONG EQUITY",
            "CALL": "CALL OVERLAY (bullish)",
            "PUT": "PUT OVERLAY (bearish / hedge — notional)",
        }[side]
        ax.text(
            0.6,
            yy - 0.6,
            f"{lab}   ${sval / 1e9:.2f}B   ·   {sval / sl['gross'] * 100:.0f}% of book   ·   {len([x for x in H if x['side'] == side])} names",
            color="white",
            fontsize=8.6,
            fontweight="bold",
            va="center",
        )
        yy -= 1.4
        for i, h in enumerate(rows):
            ax.add_patch(Rectangle((0, yy - rowh), 100, rowh, color=LT if i % 2 == 0 else "white"))
            cells = [
                (str(h.get("ticker") or "—"), 1, NAVY, "bold", "left"),
                (h["name"].title()[:20], 9, INK, "normal", "left"),
                (f"${h['value'] / 1e6:,.0f}M", 33, INK, "normal", "right"),
                (f"{h['book_pct']:.1f}%", 40, INK, "bold", "right"),
                (
                    h["action"],
                    46,
                    {"New": GREEN, "Add": GREEN, "Trim": RED, "Hold": GREY}.get(h["action"], INK),
                    "bold",
                    "center",
                ),
                (_pc(h.get("post_filing_pct")), 60, _gr(h.get("post_filing_pct")), "bold", "right"),
                (_pc(h.get("ytd")), 70, _gr(h.get("ytd")), "normal", "right"),
                (
                    (h.get("rating") or "—")[:11],
                    80,
                    GREEN if "Buy" in str(h.get("rating")) else INK,
                    "normal",
                    "left",
                ),
                (_pc(h.get("upside")), 97, _gr(h.get("upside")), "bold", "right"),
            ]
            for txt, xp, c, wt, ha in cells:
                ax.text(xp, yy - rowh / 2, txt, color=c, fontsize=7.8, ha=ha, va="center", fontweight=wt)
            yy -= rowh
        yy -= 0.5

    # WOW signals — fixed 2-col grid in the remaining space (no overlap)
    wows = (syn.get("top_wows") or [])[:6]
    if wows:
        band_top = yy - 0.2
        ax.add_patch(Rectangle((0, band_top - 1.1), 100, 1.1, color=GOLD))
        ax.text(
            0.6, band_top - 0.55, "★ WOW SIGNALS", color=NAVY, fontsize=8.8, fontweight="bold", va="center"
        )
        grid_top = band_top - 1.5
        cell_h = (grid_top - 1.6) / ((len(wows) + 1) // 2)  # fill down to y≈1.6
        for i, wv in enumerate(wows):
            cx = (i % 2) * 50 + 0.6
            cyt = grid_top - (i // 2) * cell_h
            ax.text(cx, cyt, f"★ {wv['title'][:48]}", color=BRONZE, fontsize=7.6, fontweight="bold", va="top")
            t = wv["one_liner"] + (("  — " + wv["numbers"]) if wv.get("numbers") else "")
            ax.text(cx, cyt - 0.9, textwrap.fill(t, 60), color=INK, fontsize=6.4, va="top")

    flags = ""
    if M.get("unresolved_cusips"):
        flags += f" · {len(M['unresolved_cusips'])} unresolved CUSIP"
    if M.get("enrich_errors"):
        flags += f" · no data: {','.join(e['ticker'] for e in M['enrich_errors'])}"
    ax.text(
        0.6,
        0.8,
        f"Generated {M['generated'][:16].replace('T', ' ')} UTC · {('puts/calls = underlying notional, weights within sleeve · ' if opts else '')}13F ≤45-day lag · not advice{flags}",
        color=GREY,
        fontsize=6.6,
        va="center",
    )

    plt.savefig(out_png, dpi=130, bbox_inches="tight", facecolor="white")
    plt.savefig(out_png.rsplit(".", 1)[0] + ".pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_png
