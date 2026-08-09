"""Multi-tab evolution workbook for deep mode (xlsxwriter — Excel-exact, no repair prompts).

Six tabs, all driven by `deep.run_deep` output + an optional analytical `synthesis` dict:
  1. Evolution  — the position×quarter weight matrix (manual heat-shading; entries flagged)
  2. Quarters   — per-quarter book shape, concentration, and New/Add/Trim/Exit activity
  3. Flows      — chronological event ledger with the forward-return timing signal
  4. Performance— per-bet attribution: reported-held-window return vs benchmark (alpha)
  5. Macro      — quarter × macro-event overlay (from synthesis)
  6. ManagerHead— the "inside the manager's head" narrative dossier (from synthesis)

Tabs 1-4 are deterministic from the data dict; 5-6 render the verified analytical synthesis
(and degrade to a clear "run the analytical workflow" placeholder when none is supplied).
"""

from __future__ import annotations

import xlsxwriter

NAVY, NAVY2, BRONZE, GOLD = "#1B2A4A", "#26395E", "#B0763A", "#C9A227"
LT, LT2, WHITE, INK = "#F4F1EA", "#EAE4D6", "#FFFFFF", "#1B1B1B"
GREEN, GREENL, RED, REDL, GREY = "#1E7F4F", "#D7EFDF", "#B3261E", "#F6D7D4", "#6B6B6B"
# weight heat ramp (light -> deep navy); text flips to white on the two darkest
HEAT = ["#FFFFFF", "#EAF0F7", "#CDDcEC", "#9DB6D6", "#5E80B0", "#2E4D7E"]
ACTION_COLOR = {"New": GREEN, "Add": GREENL, "Trim": REDL, "Exit": RED, "Roll": "#0B6E8F"}


def _qlabel(period: str) -> str:
    """2021-06-30 -> 2Q21 (analyst-compact quarter label)."""
    y, m, _ = period.split("-")
    return f"{ {'03': '1', '06': '2', '09': '3', '12': '4'}.get(m, '?') }Q{y[2:]}"


def _heat_fmt_idx(weight: float) -> int:
    for i, hi in enumerate((1, 3, 6, 10, 20)):
        if weight < hi:
            return i
    return 5


def _row_height(text, col_width: float, min_h: float = 18.0, line_pt: float = 14.5) -> float:
    """Points needed to show wrapped text at a column's char-width (so nothing clips). Honors \\n."""
    text = str(text or "")
    cpl = max(10, int(col_width * 1.05))  # ~chars per wrapped line (Calibri ~10pt; col width is char units)
    lines = sum(max(1, -(-len(seg) // cpl)) for seg in text.split("\n"))
    return max(min_h, lines * line_pt)


def build_deep(data: dict, out_path: str, synthesis: dict | None = None) -> str:
    M = data["meta"]
    syn = synthesis or {}
    tags = syn.get("convexity_tags", {})  # ticker -> short convexity-form tag

    wb = xlsxwriter.Workbook(out_path, {"nan_inf_to_errors": True})
    cache: dict = {}

    def f(**p):
        d = {"font_name": "Calibri", "border": 1, "border_color": "#D9D2C4"}
        d.update(p)
        k = tuple(sorted(d.items()))
        cache.setdefault(k, wb.add_format(d))
        return cache[k]

    def fnb(**p):
        return f(border=0, **p)

    def title_band(ws, ncols, sub):
        ws.merge_range(
            0,
            0,
            0,
            ncols - 1,
            f"{M['manager'].upper()}  —  DEEP-13F EVOLUTION",
            fnb(
                bold=True,
                font_color=GOLD,
                bg_color=NAVY,
                font_size=15,
                align="left",
                valign="vcenter",
                indent=1,
            ),
        )
        ws.set_row(0, 24)
        ws.merge_range(
            1,
            0,
            1,
            ncols - 1,
            sub,
            fnb(font_color=LT, bg_color=NAVY2, font_size=9, align="left", valign="vcenter", indent=1),
        )
        ws.set_row(1, 14)

    periods = data["matrix"]["periods"]
    qlabels = [_qlabel(p) for p in periods]
    sub = (
        f"{M['n_quarters']} quarters · {M['first_period']} → {M['latest_period']} · CIK {M['cik']} · "
        f"universe {M['universe_size']} names · benchmark {M['benchmark']} · SEC EDGAR 13F-HR + yfinance · "
        f"13F = 45-day-lagged quarter-END snapshot (no intra-quarter trades / fills visible)"
    )

    # ============================================================ TAB 1 — Evolution matrix
    ws = wb.add_worksheet("Evolution")
    ws.hide_gridlines(2)
    rows = data["matrix"]["rows"]
    LEAD = 3  # ticker, name, convexity tag
    ncols = LEAD + len(periods)
    title_band(ws, ncols, sub)
    ws.set_column(0, 0, 8)
    ws.set_column(1, 1, 24)
    ws.set_column(2, 2, 20)
    ws.set_column(LEAD, ncols - 1, 5.4)

    # focus the grid on names that tell the story: drop the noise floor (peak <1% of book, held <3
    # quarters, not currently held — e.g. the rare $5k option speck). Full set stays in the JSON.
    MIN_PEAK = 1.0
    shown = [r for r in rows if r["still_held"] or r["peak_weight"] >= MIN_PEAK or r["n_quarters_held"] >= 3]
    omitted = len(rows) - len(shown)
    rows = shown

    r = 3
    hdr = f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=8, align="center", valign="vcenter")
    ws.write(r, 0, "Ticker", f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=8))
    ws.write(r, 1, "Name", f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=8))
    ws.write(r, 2, "Convexity form", f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=8))
    for j, ql in enumerate(qlabels):
        ws.write(r, LEAD + j, ql, hdr)
    ws.set_row(r, 22)
    ws.freeze_panes(r + 1, LEAD)
    r += 1

    name_f = f(font_size=8, font_color=INK, align="left")
    tag_f = f(font_size=7, font_color=BRONZE, align="left", italic=True)
    for row in rows:
        ws.write(r, 0, row["ticker"] or "—", f(bold=True, font_size=8, font_color=NAVY, align="left"))
        ws.write(r, 1, row["name"].title()[:30], name_f)
        ws.write(r, 2, tags.get(row["ticker"], "") or "", tag_f)
        for j, p in enumerate(periods):
            cell = row["cells"].get(p)
            if not cell:
                ws.write_blank(r, LEAD + j, None, f(bg_color="#FBFAF7"))
                continue
            wt = cell["book_pct"]  # % of GROSS book (comparable across sleeves; long-only book ≈ % of long)
            idx = _heat_fmt_idx(wt)
            fc = WHITE if idx >= 4 else INK
            is_entry = p == row["first_seen"]
            cf = f(
                bg_color=HEAT[idx],
                font_color=fc,
                font_size=8,
                align="center",
                num_format="0.0",
                bold=is_entry,
                **({"left": 5, "left_color": GOLD} if is_entry else {}),
            )
            ws.write_number(r, LEAD + j, round(wt, 1), cf)
        r += 1
    # legend
    r += 1
    ws.write(
        r,
        0,
        "Cells = % of gross book (≈ % of long book; manager is long-only).  "
        "GOLD left-edge + bold = initiation quarter.  Blank = not held.  "
        f"({omitted} sub-1% short-lived names omitted from this grid; all are in the data.)",
        fnb(font_size=8, italic=True, font_color=GREY),
    )

    # ============================================================ TAB 2 — Quarters
    ws2 = wb.add_worksheet("Quarters")
    ws2.hide_gridlines(2)
    cols2 = [
        "Quarter",
        "Filed",
        "13F AUM",
        "# Pos",
        "Top-5 %",
        "Top-10 %",
        "HHI",
        "Eff N",
        "New",
        "Add",
        "Trim",
        "Exit",
        "Buy ~$",
        "Sell ~$",
    ]
    title_band(
        ws2,
        len(cols2),
        "Book shape & activity per quarter — concentration, breadth, gross buying/selling (est.)",
    )
    widths2 = [9, 11, 12, 7, 8, 8, 8, 7, 6, 6, 6, 6, 12, 12]
    for i, w in enumerate(widths2):
        ws2.set_column(i, i, w)
    r = 3
    for j, c in enumerate(cols2):
        ws2.write(r, j, c, f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=9, align="center"))
    ws2.set_row(r, 20)
    ws2.freeze_panes(r + 1, 1)
    r += 1
    money = f(num_format='$#,##0,,"M"', font_size=9, align="right")
    ctr = f(font_size=9, align="center")
    for q in data["quarters"]:
        pa = q["pareto"]
        ws2.write(r, 0, _qlabel(q["period"]), f(bold=True, font_size=9, font_color=NAVY, align="center"))
        ws2.write(r, 1, q["filed"], ctr)
        ws2.write_number(r, 2, q["sleeves"]["long"], money)
        ws2.write_number(r, 3, q["n_positions"], ctr)
        ws2.write_number(r, 4, round(pa["top5"], 1), f(num_format="0.0", font_size=9, align="center"))
        ws2.write_number(r, 5, round(pa["top10"], 1), f(num_format="0.0", font_size=9, align="center"))
        ws2.write_number(r, 6, round(pa["hhi"]), ctr)
        ws2.write_number(r, 7, round(pa["effective_n"], 1), f(num_format="0.0", font_size=9, align="center"))
        ws2.write_number(
            r, 8, q["n_new"], f(font_size=9, align="center", font_color=GREEN, bold=q["n_new"] > 0)
        )
        ws2.write_number(r, 9, q["n_add"], ctr)
        ws2.write_number(r, 10, q["n_trim"], ctr)
        ws2.write_number(
            r, 11, q["n_exit"], f(font_size=9, align="center", font_color=RED, bold=q["n_exit"] > 0)
        )
        ws2.write_number(r, 12, q["buy_est"], money)
        ws2.write_number(r, 13, q["sell_est"], money)
        r += 1
    # footnotes: corrections + excluded filings (honest data-provenance trail)
    r += 1
    for sc in M.get("scale_corrections", []):
        ws2.merge_range(
            r,
            0,
            r,
            len(cols2) - 1,
            f"ⓘ {_qlabel(sc['period'])} ({sc['period']}): values reported {sc['factor']}× off — "
            f"rescaled (a uniform unit error; weights unaffected).",
            fnb(font_size=8, italic=True, font_color=BRONZE),
        )
        r += 1
    for ex in M.get("excluded_filings", []):
        ws2.merge_range(
            r,
            0,
            r,
            len(cols2) - 1,
            f"⚠ {_qlabel(ex['period'])} ({ex['period']}) EXCLUDED from the timeline — {ex['reason']} "
            f"(raw total as filed ${ex['raw_total'] / 1e9:.1f}B).",
            fnb(font_size=8, italic=True, font_color=RED),
        )
        r += 1

    # ============================================================ TAB 3 — Flows ledger
    ws3 = wb.add_worksheet("Flows")
    ws3.hide_gridlines(2)
    cols3 = [
        "Quarter",
        "Action",
        "Ticker",
        "Name",
        "Side",
        "Value",
        "Wt %",
        "Δ Shares",
        "Fwd 1Q %",
        "→ Now %",
    ]
    title_band(
        ws3,
        len(cols3),
        "Every New/Add/Trim/Exit, newest first — with the stock's forward move (timing signal)",
    )
    widths3 = [9, 8, 8, 26, 6, 13, 7, 12, 9, 9]
    for i, w in enumerate(widths3):
        ws3.set_column(i, i, w)
    r = 3
    for j, c in enumerate(cols3):
        ws3.write(r, j, c, f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=9, align="center"))
    ws3.freeze_panes(r + 1, 0)
    r += 1

    def pct_cell(ws, rr, cc, v):
        if v is None:
            ws.write(rr, cc, "—", f(font_size=9, align="center", font_color=GREY))
        else:
            col = GREEN if v >= 0 else RED
            ws.write_number(
                rr, cc, round(v, 1), f(num_format="+0.0;-0.0", font_size=9, align="center", font_color=col)
            )

    for ev in sorted(data["flows"], key=lambda e: (e["period"], -(e["value"] or 0)), reverse=True):
        ac = ev["action"]
        ws3.write(r, 0, _qlabel(ev["period"]), f(font_size=9, align="center", font_color=NAVY))
        ws3.write(
            r,
            1,
            ac,
            f(
                font_size=9,
                align="center",
                bold=True,
                bg_color=ACTION_COLOR.get(ac, WHITE),
                font_color=WHITE if ac in ("New", "Exit") else INK,
            ),
        )
        ws3.write(r, 2, ev["ticker"] or "—", f(font_size=9, align="left", bold=True, font_color=NAVY))
        ws3.write(r, 3, ev["name"].title()[:32], f(font_size=9, align="left"))
        ws3.write(r, 4, ev["side"], f(font_size=8, align="center", font_color=GREY))
        ws3.write_number(r, 5, ev["value"], f(num_format='$#,##0,,"M"', font_size=9, align="right"))
        if ev.get("weight") is not None:
            ws3.write_number(r, 6, round(ev["weight"], 1), f(num_format="0.0", font_size=9, align="center"))
        else:
            ws3.write(r, 6, "—", f(font_size=9, align="center", font_color=GREY))
        ws3.write_number(
            r, 7, ev["delta_shares"] or 0, f(num_format="#,##0;(#,##0)", font_size=9, align="right")
        )
        pct_cell(ws3, r, 8, ev.get("fwd_1q_pct"))
        pct_cell(ws3, r, 9, ev.get("fwd_to_now_pct"))
        r += 1

    # ============================================================ TAB 4 — Performance attribution
    ws4 = wb.add_worksheet("Performance")
    ws4.hide_gridlines(2)
    cols4 = [
        "Ticker",
        "Name",
        "Status",
        "Entry Q",
        "Exit",
        "Held",
        "Entry $",
        "Exit $",
        "Hold Ret %",
        f"{M['benchmark']} %",
        "Alpha %",
        "Avg Wt",
        "Contrib α",
    ]
    title_band(
        ws4,
        len(cols4),
        "Per-bet attribution (entry quarter-end → last-held / now), split+div adjusted, vs benchmark over the same "
        "window. Held = quarters held/span (* = non-contiguous → return is a price path incl. quarters NOT held). "
        "Contrib α = avg book-weight × alpha = which bets MOVED the book.",
    )
    widths4 = [8, 22, 7, 8, 8, 7, 9, 9, 10, 10, 10, 8, 9]
    for i, w in enumerate(widths4):
        ws4.set_column(i, i, w)
    r = 3
    for j, c in enumerate(cols4):
        ws4.write(r, j, c, f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=9, align="center"))
    ws4.freeze_panes(r + 1, 0)
    r += 1
    perf_sorted = sorted(
        data["performance"],
        key=lambda p: (p["alpha_pct"] if p["alpha_pct"] is not None else -1e9),
        reverse=True,
    )
    for p in perf_sorted:
        ws4.write(r, 0, p["ticker"] or "—", f(font_size=9, align="left", bold=True, font_color=NAVY))
        ws4.write(r, 1, p["name"].title()[:30], f(font_size=9, align="left"))
        ws4.write(
            r,
            2,
            "Held" if p["still_held"] else "Closed",
            f(font_size=8, align="center", font_color=GREEN if p["still_held"] else GREY),
        )
        ws4.write(r, 3, _qlabel(p["entry_period"]), f(font_size=9, align="center"))
        ws4.write(
            r,
            4,
            "now" if p["exit_period"] == "now" else _qlabel(p["exit_period"]),
            f(font_size=9, align="center"),
        )
        span = p["n_quarters_held"] + p.get("n_gaps", 0)
        gapped = not p.get("contiguous", True)
        ws4.write(
            r,
            5,
            f"{p['n_quarters_held']}/{span}{'*' if gapped else ''}",
            f(font_size=9, align="center", font_color=BRONZE if gapped else INK),
        )
        for cc, key in ((6, "entry_close"), (7, "exit_close")):
            v = p.get(key)
            ws4.write_number(
                r, cc, round(v, 2), f(num_format="$#,##0.00", font_size=9, align="right")
            ) if v is not None else ws4.write(r, cc, "—", f(font_size=9, align="center", font_color=GREY))
        for cc, key in ((8, "holding_return_pct"), (9, "bench_return_pct"), (10, "alpha_pct")):
            v = p.get(key)
            if v is None:
                ws4.write(r, cc, "—", f(font_size=9, align="center", font_color=GREY))
            else:
                col = GREEN if v >= 0 else RED
                ws4.write_number(
                    r,
                    cc,
                    round(v, 1),
                    f(num_format="+0.0;-0.0", font_size=9, align="center", font_color=col, bold=(cc == 10)),
                )
        ws4.write_number(
            r, 11, round(p.get("avg_held_weight") or 0, 1), f(num_format="0.0", font_size=9, align="center")
        )
        ca = p.get("contrib_alpha")
        if ca is None:
            ws4.write(r, 12, "—", f(font_size=9, align="center", font_color=GREY))
        else:
            ws4.write_number(
                r,
                12,
                round(ca, 1),
                f(
                    num_format="+0.0;-0.0",
                    font_size=9,
                    align="center",
                    bold=True,
                    font_color=GREEN if ca >= 0 else RED,
                ),
            )
        r += 1

    # ============================================================ TAB 5 — Macro timeline
    ws5 = wb.add_worksheet("Macro")
    ws5.hide_gridlines(2)
    cols5 = ["Quarter", "Macro regime / beat", "What the manager did", "The read"]
    title_band(
        ws5,
        len(cols5),
        "Quarter × macro-event overlay — the events the manager positioned into (analytical synthesis)",
    )
    for i, w in enumerate([9, 34, 40, 46]):
        ws5.set_column(i, i, w)
    r = 3
    for j, c in enumerate(cols5):
        ws5.write(r, j, c, f(bold=True, font_color=WHITE, bg_color=NAVY2, font_size=9, align="center"))
    ws5.freeze_panes(r + 1, 1)
    r += 1
    mt = syn.get("macro_timeline") or []
    wrap = f(font_size=9, align="left", valign="top", text_wrap=True)
    if mt:
        for e in mt:
            ws5.write(
                r,
                0,
                _qlabel(e.get("period", "")) if e.get("period") else "",
                f(font_size=9, align="center", bold=True, font_color=NAVY),
            )
            ws5.write(r, 1, e.get("macro", ""), wrap)
            ws5.write(r, 2, e.get("dan_moves", ""), wrap)
            ws5.write(r, 3, e.get("read", ""), wrap)
            ws5.set_row(  # size to the tallest of the three wrapped columns so nothing clips
                r,
                max(
                    _row_height(e.get("macro", ""), 34),
                    _row_height(e.get("dan_moves", ""), 40),
                    _row_height(e.get("read", ""), 46),
                    36,
                ),
            )
            r += 1
    else:
        ws5.merge_range(
            r,
            0,
            r,
            len(cols5) - 1,
            "Run the deep-13F analytical workflow to populate the macro overlay (synthesis.macro_timeline).",
            f(font_size=10, italic=True, font_color=GREY, align="left", indent=1),
        )

    # ============================================================ TAB 6 — Manager's head
    ws6 = wb.add_worksheet("ManagerHead")
    ws6.hide_gridlines(2)
    title_band(
        ws6,
        2,
        "The 'inside the manager's head' narrative — conviction arcs, rotations, the doctrine in the book",
    )
    ws6.set_column(0, 0, 26)
    ws6.set_column(1, 1, 96)
    r = 3
    nar = syn.get("narrative") or {}
    h_f = f(bold=True, font_color=WHITE, bg_color=BRONZE, font_size=11, align="left", indent=1)
    k_f = f(bold=True, font_color=NAVY, font_size=10, align="left", valign="top", indent=1)
    v_f = f(font_size=10, align="left", valign="top", text_wrap=True)

    def section(label, body):
        nonlocal r
        ws6.merge_range(r, 0, r, 1, label, h_f)
        ws6.set_row(r, 20)
        r += 1
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    key = item.get("theme") or item.get("ticker") or ""
                    val = item.get("detail") or item.get("note") or ""
                else:
                    key, val = "", str(item)
                ws6.write(r, 0, key, k_f)
                ws6.write(r, 1, val, v_f)
                # size to the taller of the label (w26) and the body (w96) so nothing clips
                ws6.set_row(r, max(_row_height(key, 26, 24), _row_height(val, 96, 24)))
                r += 1
        else:
            ws6.write(r, 0, "", k_f)
            ws6.write(r, 1, str(body), v_f)
            ws6.set_row(r, _row_height(str(body), 96, 40))
            r += 1

    if nar:
        if nar.get("headline"):
            section("HEADLINE", nar["headline"])
        if nar.get("conviction_arc"):
            section("CONVICTION ARC", nar["conviction_arc"])
        if nar.get("rotation_themes"):
            section("ROTATION THEMES", nar["rotation_themes"])
        if nar.get("convexity_mapping"):
            section("CONVEXITY DOCTRINE IN HIS BOOK", nar["convexity_mapping"])
        if nar.get("psychological_read"):
            section("THE PSYCHOLOGICAL READ", nar["psychological_read"])
        if nar.get("caveats"):
            section("HONEST CAVEATS", nar["caveats"])
    else:
        ws6.merge_range(
            r,
            0,
            r,
            1,
            "Run the deep-13F analytical workflow to populate the narrative (synthesis.narrative).",
            f(font_size=11, italic=True, font_color=GREY, align="left", indent=1),
        )

    wb.close()
    return out_path
