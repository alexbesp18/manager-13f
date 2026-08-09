"""Adaptive one-page xlsx builder (xlsxwriter — Excel-exact, no repair prompts).

Driven entirely by pipeline.run() output + an optional `synthesis` dict (theme,
thesis_buckets, top_wows, headline_stats, validated). Detects an options book and
renders LONG / CALL / PUT sleeves with subtotals; a long-only book collapses to a
single clean holdings table. Weights are always within-sleeve.
"""

from __future__ import annotations

import xlsxwriter

NAVY, NAVY2, BRONZE, GOLD = "#1B2A4A", "#26395E", "#B0763A", "#C9A227"
LT, LT2, WHITE, INK = "#F4F1EA", "#EAE4D6", "#FFFFFF", "#1B1B1B"
GREEN, GREENL, RED, REDL, GREY = "#1E7F4F", "#D7EFDF", "#B3261E", "#F6D7D4", "#6B6B6B"
PCTI = "+0%;-0%;0%"
SIDE_COLOR = {"LONG": GREEN, "CALL": "#0B6E8F", "PUT": RED}


def _patch_analyst(holdings, synthesis):
    patch = {
        p["ticker"]: p
        for p in (synthesis or {}).get("validated", [])
        if isinstance(p, dict) and p.get("ticker")
    }
    for h in holdings:
        p = patch.get(h.get("ticker"))
        if not p:
            continue
        current_rating = h.get("rating")
        if (not current_rating or str(current_rating).lower() == "none") and p.get("consensus_rating"):
            h["rating"] = p["consensus_rating"]
        if not h.get("n_analysts") and p.get("n_analysts"):
            h["n_analysts"] = p["n_analysts"]
        if not h.get("target_mean") and p.get("mean_target"):
            h["target_mean"] = p["mean_target"]


def build(data: dict, out_path: str, synthesis: dict | None = None) -> str:
    M, H, X = data["meta"], data["holdings"], data["exits"]
    syn = (synthesis or {}).get("synthesis", {})
    _patch_analyst(H, synthesis)
    opts = M["is_options_book"]
    sl = M["sleeves"]

    wb = xlsxwriter.Workbook(out_path, {"nan_inf_to_errors": True})
    ws = wb.add_worksheet("13F")
    ws.hide_gridlines(2)
    cache = {}

    def f(**p):
        d = {"font_name": "Calibri", "border": 1, "border_color": "#C9C2B4"}
        d.update(p)
        k = tuple(sorted(d.items()))
        cache.setdefault(k, wb.add_format(d))
        return cache[k]

    def fnb(**p):
        return f(border=0, **p)

    NC = 22
    widths = [4, 6, 7, 21, 11, 11, 11, 7, 7, 8, 8, 9, 9, 9, 7, 6, 7, 7, 11, 4, 9, 7]
    for i, w in enumerate(widths):
        ws.set_column(i, i, w)

    def mc(r, c1, c2, text, fmt):
        ws.write(r, c1, text, fmt) if c1 == c2 else ws.merge_range(r, c1, r, c2, text, fmt)

    r = 0
    mc(
        r,
        0,
        NC - 1,
        f"{M['manager'].upper()}  —  13F INTELLIGENCE",
        fnb(
            bold=True, font_color=GOLD, bg_color=NAVY, font_size=16, align="left", valign="vcenter", indent=1
        ),
    )
    ws.set_row(r, 26)
    r += 1
    book = "LONG/SHORT OPTIONS BOOK" if opts else "LONG EQUITY BOOK"
    mc(
        r,
        0,
        NC - 1,
        f"{book}  ·  {M['latest_period']} (filed {M['latest_filed']})  ·  prior {M['prior_period']}  ·  "
        f"CIK {M['cik']}  ·  SEC EDGAR 13F-HR + yfinance",
        fnb(font_color=LT, bg_color=NAVY2, font_size=9, align="left", valign="vcenter", indent=1),
    )
    ws.set_row(r, 15)
    r += 1

    # ---- KPI strip (sleeve-aware) ----
    spY = M["sleeves_prior"]
    gross_chg = (sl["gross"] / spY["gross"] - 1) * 100 if spY.get("gross") else None
    if opts:
        kpis = [
            ("Gross 13F", f"${sl['gross'] / 1e9:.1f}B", GOLD),
            ("Long", f"${sl['long'] / 1e9:.2f}B", GREENL),
            ("Call ovl", f"${sl['call'] / 1e9:.2f}B", "#BEE3F8"),
            ("Put ovl", f"${sl['put'] / 1e9:.2f}B", REDL),
            ("Net (L+C−P)", f"${sl['net_long'] / 1e9:+.2f}B", REDL if sl["net_long"] < 0 else GREENL),
            ("Positions", f"{M['n_positions']}", WHITE),
            ("QoQ gross", f"{gross_chg:+.0f}%" if gross_chg is not None else "—", WHITE),
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
            ("Positions", f"{M['n_positions']}", WHITE),
            ("QoQ AUM", f"{gross_chg:+.0f}%" if gross_chg is not None else "—", WHITE),
            ("New / Exited", f"{sum(1 for h in H if h['action'] == 'New')} / {len(X)}", LT2),
            ("Top-5 wt", f"{p['top5']:.0f}%", WHITE),
            ("HHI", f"{p['hhi']:,.0f}", LT2),
            ("Eff. names", f"{p['effective_n']:.0f}", WHITE),
            (f"{M['benchmark']} YTD", f"+{M['benchmark_ytd']:.0f}%", LT2),
        ]
    kw = NC // len(kpis)
    c0 = 0
    for i, (lab, val, vc) in enumerate(kpis):
        c2 = NC - 1 if i == len(kpis) - 1 else c0 + kw - 1
        mc(
            r,
            c0,
            c2,
            lab.upper(),
            fnb(bold=True, font_color=LT2, bg_color=NAVY2, font_size=7.5, align="center", valign="vcenter"),
        )
        mc(
            r + 1,
            c0,
            c2,
            val,
            fnb(
                bold=True,
                font_color=GOLD if vc == WHITE else vc,
                bg_color=NAVY,
                font_size=12.5,
                align="center",
                valign="vcenter",
            ),
        )
        c0 = c2 + 1
    ws.set_row(r, 13)
    ws.set_row(r + 1, 22)
    r += 2

    if syn.get("theme_headline"):
        mc(
            r,
            0,
            NC - 1,
            "★ THESIS:  " + syn["theme_headline"],
            fnb(
                bold=True,
                font_color=NAVY,
                bg_color=GOLD,
                font_size=10.5,
                align="left",
                valign="vcenter",
                indent=1,
            ),
        )
        ws.set_row(r, 18)
        r += 1
    r += 1

    # ---- holdings table ----
    title = (
        "▎ HOLDINGS — grouped by sleeve; % Book = weight in the TOTAL gross book"
        if opts
        else f"▎ CORE HOLDINGS — {M['n_positions']} positions (sorted by value; Pareto cum% flags 80/20)"
    )
    mc(
        r,
        0,
        NC - 1,
        title,
        fnb(
            bold=True,
            font_color=WHITE,
            bg_color=BRONZE,
            font_size=10,
            align="left",
            valign="vcenter",
            indent=1,
        ),
    )
    ws.set_row(r, 16)
    r += 1
    heads = [
        "#",
        "Side",
        "Ticker",
        "Company",
        "Sector",
        "Shares",
        "Value $",
        "% Book",
        "Cum%",
        "Action",
        "QoQ sh%",
        "Filing $",
        "Price $",
        "PostFil%",
        "YTD %",
        "RSI",
        "%>200d",
        "%<52wH",
        "Analyst",
        "#An",
        "Tgt $",
        "Up%",
    ]
    hf = fnb(
        bold=True,
        font_color=WHITE,
        bg_color=NAVY,
        font_size=7.6,
        align="center",
        valign="vcenter",
        text_wrap=True,
    )
    for j, h in enumerate(heads):
        ws.write(r, j, h, hf)
    ws.set_row(r, 23)
    data_start = r + 1
    rr = data_start
    cf_rows = []  # (row) for conditional formatting on numeric cols

    sleeves_order = ["LONG", "CALL", "PUT"] if opts else ["LONG"]
    cum = 0  # cumulative % of TOTAL book, continuous across sleeves
    for side in sleeves_order:
        srows = [h for h in H if h["side"] == side]
        if not srows:
            continue
        if opts:  # sleeve header band
            sval = sum(x["value"] for x in srows)
            lab = {
                "LONG": "LONG EQUITY (bullish, real capital)",
                "CALL": "CALL OVERLAY (bullish leverage — notional)",
                "PUT": "PUT OVERLAY (bearish / hedge — underlying notional)",
            }[side]
            mc(
                rr,
                0,
                NC - 1,
                f"  {lab}   ·   ${sval / 1e9:.2f}B   ·   {sval / sl['gross'] * 100:.0f}% of book   ·   {len(srows)} names",
                fnb(
                    bold=True,
                    font_color=WHITE,
                    bg_color=SIDE_COLOR[side],
                    font_size=8.5,
                    align="left",
                    valign="vcenter",
                    indent=1,
                ),
            )
            ws.set_row(rr, 14)
            rr += 1
        for i, h in enumerate(srows):
            cum += h["book_pct"]  # continuous cumulative % of the TOTAL book across all sleeves
            bg = LT if i % 2 == 0 else WHITE

            def cw(c, v, num=None, color=INK, bold=False, al="center", size=8.2, _bg=bg, _rr=rr):
                fmt = f(
                    bg_color=_bg,
                    font_color=color,
                    bold=bold,
                    align=al,
                    valign="vcenter",
                    font_size=size,
                    **({"num_format": num} if num else {}),
                )
                ws.write_blank(_rr, c, None, fmt) if v is None else ws.write(_rr, c, v, fmt)

            cw(0, i + 1, color=GREY)
            cw(1, side, color=SIDE_COLOR[side], bold=True, size=7.4)
            cw(2, h.get("ticker") or "—", color=NAVY, bold=True, size=8.6)
            cw(3, h["name"].title()[:24], al="left", size=8)
            cw(4, (h.get("sector") or "")[:12], al="left", color=GREY, size=7.2)
            cw(5, h["shares"], num="#,##0")
            cw(6, h["value"], num="$#,##0")
            cw(7, h["book_pct"] / 100, num="0.0%", bold=True)
            cw(8, cum / 100, num="0.0%", color=GREY, size=8)
            ac = {"New": GREEN, "Add": GREEN, "Trim": RED, "Hold": GREY}.get(h["action"], INK)
            cw(9, h["action"], color=ac, bold=True, size=7.8)
            # clamp display for absurd QoQ from a tiny prior stub (e.g. 1-share -> +20,000,000%)
            _q = h["qoq_shares"]
            cw(10, None if _q is None else max(min(_q, 999), -999) / 100, num=PCTI)
            cw(11, h.get("filing_px"), num="$#,##0")
            cw(12, h.get("price"), num="$#,##0")
            cw(13, None if h.get("post_filing_pct") is None else h["post_filing_pct"] / 100, num=PCTI)
            cw(14, None if h.get("ytd") is None else h["ytd"] / 100, num=PCTI)
            cw(15, None if h.get("rsi14") is None else round(h["rsi14"]))
            cw(16, None if h.get("pct_vs_ma200") is None else h["pct_vs_ma200"] / 100, num=PCTI)
            cw(17, None if h.get("pct_from_hi") is None else h["pct_from_hi"] / 100, num="0%")
            rt = h.get("rating") or "—"
            cw(18, rt, color=GREEN if "Buy" in str(rt) else INK, bold=True, size=7.2)
            cw(19, h.get("n_analysts") or "—")
            cw(20, h.get("target_mean"), num="$#,##0")
            cw(21, None if h.get("upside") is None else h["upside"] / 100, num=PCTI)
            cf_rows.append(rr)
            rr += 1
    data_end = rr - 1

    # conditional formatting across all numeric data rows
    if cf_rows:
        lo, hi = min(cf_rows), max(cf_rows)
        ws.conditional_format(
            lo,
            7,
            hi,
            7,
            {"type": "data_bar", "bar_color": GOLD, "min_type": "num", "min_value": 0, "max_type": "max"},
        )
        for col in (13, 14, 16, 21):
            ws.conditional_format(
                lo,
                col,
                hi,
                col,
                {
                    "type": "3_color_scale",
                    "min_type": "num",
                    "min_value": -0.4,
                    "min_color": REDL,
                    "mid_type": "num",
                    "mid_value": 0,
                    "mid_color": WHITE,
                    "max_type": "num",
                    "max_value": 0.6,
                    "max_color": GREENL,
                },
            )
        ws.conditional_format(
            lo,
            15,
            hi,
            15,
            {
                "type": "cell",
                "criteria": ">=",
                "value": 70,
                "format": f(bg_color=REDL, font_color=RED, bold=True, align="center", font_size=8.2),
            },
        )
        ws.conditional_format(
            lo,
            15,
            hi,
            15,
            {
                "type": "cell",
                "criteria": "<=",
                "value": 35,
                "format": f(bg_color=GREENL, font_color=GREEN, bold=True, align="center", font_size=8.2),
            },
        )
    r = data_end + 2

    # ---- lower panels ----
    def ph(rr, c1, c2, text, bgc=BRONZE, fc=WHITE):
        mc(
            rr,
            c1,
            c2,
            text,
            fnb(
                bold=True,
                font_color=fc,
                bg_color=bgc,
                font_size=9.3,
                align="left",
                valign="vcenter",
                indent=1,
            ),
        )
        ws.set_row(rr, 15)

    top = r
    # Panel A: whole-book composition by bucket, weights as % of GROSS (cols 0-6)
    ph(top, 0, 6, "▎ BOOK BY BUCKET (% of gross)")
    rr2 = top + 1
    buckets = list(syn.get("thesis_buckets") or [])
    if not opts:  # long-only: gross == long sleeve; recompute from membership (single side, safe)
        wt = {h["ticker"]: h["book_pct"] for h in H if h["side"] == "LONG"}
        for b in buckets:
            tk = [t.strip() for t in b["tickers"].replace(";", ",").split(",") if t.strip() in wt]
            if tk:
                b["pct"] = sum(wt[t] for t in tk)
    if not buckets:  # fallback: raw sleeve split
        buckets = [
            {"bucket": "PUT overlay", "tickers": "", "pct": sl["put"] / sl["gross"] * 100},
            {"bucket": "LONG equity", "tickers": "", "pct": sl["long"] / sl["gross"] * 100},
            {"bucket": "CALL overlay", "tickers": "", "pct": sl["call"] / sl["gross"] * 100},
        ]
    for i, b in enumerate(sorted(buckets, key=lambda x: -x["pct"])[:7]):
        bg = LT if i % 2 == 0 else WHITE
        mc(
            rr2,
            0,
            3,
            b["bucket"][:30],
            f(
                bg_color=bg,
                bold=True,
                font_color=NAVY,
                font_size=7.4,
                align="left",
                valign="vcenter",
                text_wrap=True,
            ),
        )
        mc(
            rr2,
            4,
            6,
            f"{b['pct']:.1f}%  {b['tickers'][:16]}",
            f(bg_color=bg, font_size=7, align="left", valign="vcenter"),
        )
        rr2 += 1
    if opts:  # net exposure with the delta caveat (notional ≠ delta-adjusted)
        if sl["put"]:
            net_note = (
                f"  NET {sl['net_long'] / 1e9:+.2f}B  ·  "
                "notional only; delta-adj. far smaller (OTM puts ≪1.0Δ)"
            )
        elif sl["call"]:
            net_note = (
                f"  NET {sl['net_long'] / 1e9:+.2f}B  ·  "
                "no puts reported; calls are underlying notional, not premium"
            )
        else:
            net_note = f"  NET {sl['net_long'] / 1e9:+.2f}B"
        mc(
            rr2,
            0,
            6,
            net_note,
            fnb(
                bold=True,
                font_color=WHITE,
                bg_color=NAVY2,
                font_size=7.2,
                align="left",
                valign="vcenter",
                indent=1,
            ),
        )
        rr2 += 1
    # Pareto sub-panel
    p = M["pareto_long"]
    ph(rr2, 0, 6, "▎ LONG-BOOK PARETO & CONCENTRATION")
    rr2 += 1
    facts = [
        (f"{p['n80']} names = 80% of long book", f"{p['n80']}/{p['n']}"),
        (f"{p['n50']} names = 50%", f"top {p['n50']}"),
        ("Top-5 / Top-10", f"{p['top5']:.0f}% / {p['top10']:.0f}%"),
        ("HHI / Effective names", f"{p['hhi']:,.0f} / {p['effective_n']:.1f}"),
    ]
    for i, (k, v) in enumerate(facts):
        bg = LT if i % 2 == 0 else WHITE
        mc(rr2, 0, 3, k, f(bg_color=bg, font_size=8, align="left", valign="vcenter"))
        mc(
            rr2,
            4,
            6,
            v,
            f(bg_color=bg, bold=True, font_color=NAVY, font_size=8, align="center", valign="vcenter"),
        )
        rr2 += 1
    left_bottom = rr2

    # Panel B: QoQ activity + exits (cols 7-12)
    ph(top, 7, 12, "▎ QoQ ACTIVITY")
    rr3 = top + 1
    acts = [("New", GREEN), ("Add", GREEN), ("Trim", RED), ("Hold", GREY)]
    for i, (a, col) in enumerate(acts):
        names = ", ".join(f"{h['ticker'] or h['name'][:6]}" for h in H if h["action"] == a)[:60]
        bg = LT if i % 2 == 0 else WHITE
        ws.write(
            rr3,
            7,
            a,
            f(bg_color=bg, bold=True, font_color=col, font_size=7.8, align="left", valign="vcenter"),
        )
        mc(
            rr3,
            8,
            12,
            names or "—",
            f(bg_color=bg, font_size=7, align="left", valign="vcenter", text_wrap=True),
        )
        ws.set_row(rr3, 20)
        rr3 += 1
    ph(rr3, 7, 12, "▎ EXITS (gone vs prior Q)")
    rr3 += 1
    moves = [e["since_exit_pct"] for e in X if e.get("since_exit_pct") is not None]
    if moves:
        rallied = sum(1 for m in moves if m > 0)
        mc(rr3, 7, 9, "Sold-then-rose", f(bg_color=LT, font_size=7.8, align="left", valign="vcenter"))
        mc(
            rr3,
            10,
            12,
            f"{rallied}/{len(moves)} · avg {sum(moves) / len(moves):+.0f}%",
            f(
                bg_color=LT,
                bold=True,
                font_color=RED if sum(moves) > 0 else GREEN,
                font_size=7.8,
                align="center",
                valign="vcenter",
            ),
        )
        rr3 += 1
    for i, e in enumerate(X[:4]):
        bg = LT if i % 2 == 0 else WHITE
        sx = "" if e.get("since_exit_pct") is None else f"{e['since_exit_pct']:+.0f}%"
        mc(
            rr3,
            7,
            10,
            f"{e.get('ticker') or e['name'][:14]} ({e['side']})",
            f(bg_color=bg, font_size=7.2, align="left", valign="vcenter"),
        )
        mc(
            rr3,
            11,
            12,
            sx,
            f(bg_color=bg, bold=True, font_color=GREY, font_size=7.2, align="center", valign="vcenter"),
        )
        rr3 += 1
    mid_bottom = rr3

    # Panel C: WOW signals (cols 13-21)
    ph(top, 13, NC - 1, "▎ ★ WOW SIGNALS", bgc=GOLD, fc=NAVY)
    rr4 = top + 1
    wows = syn.get("top_wows") or []
    for i, w in enumerate(wows[:6]):
        bg = LT2 if i % 2 == 0 else WHITE
        mc(
            rr4,
            13,
            NC - 1,
            f"★ {w['title']}",
            f(
                bg_color=bg,
                bold=True,
                font_color=BRONZE,
                font_size=8.4,
                align="left",
                valign="vcenter",
                indent=1,
            ),
        )
        rr4 += 1
        txt = w["one_liner"] + ("  —  " + w["numbers"] if w.get("numbers") else "")
        mc(
            rr4,
            13,
            NC - 1,
            txt,
            f(
                bg_color=bg,
                font_color=INK,
                font_size=7.6,
                align="left",
                valign="top",
                text_wrap=True,
                indent=1,
            ),
        )
        ws.set_row(rr4, 26)
        rr4 += 1
    right_bottom = rr4
    r = max(left_bottom, mid_bottom, right_bottom) + 1

    # Key reads
    stats = syn.get("headline_stats") or []
    if stats:
        ph(r, 0, NC - 1, "▎ KEY READS", bgc=NAVY2)
        r += 1
        half = (len(stats) + 1) // 2
        for i in range(half):
            bg = LT if i % 2 == 0 else WHITE
            mc(
                r,
                0,
                10,
                "▸ " + stats[i],
                f(bg_color=bg, font_size=8, align="left", valign="vcenter", indent=1),
            )
            rv = stats[i + half] if i + half < len(stats) else ""
            mc(
                r,
                11,
                NC - 1,
                ("▸ " + rv) if rv else "",
                f(bg_color=bg, font_size=8, align="left", valign="vcenter", indent=1),
            )
            ws.set_row(r, 14)
            r += 1
        r += 1

    # footer
    note = (
        "Put/Call lines report the market value of the UNDERLYING shares (notional), NOT option premium; "
        "% Book = weight in the total gross book (long + call + put notional). Net exposure = long + call − put. "
        if opts
        else ""
    )
    filing_url = (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{str(M['cik']).lstrip('0')}/{M['latest_accession'].replace('-', '')}/"
    )
    mc(
        r,
        0,
        NC - 1,
        note + "13F = long US equity/ADR + listed options snapshot, lagged up to 45 days. "
        "PostFil% = mark-to-market vs the 13F implied price (value/shares). "
        f"Source: SEC EDGAR {filing_url}; technicals/analyst from yfinance + web checks. Not investment advice.",
        fnb(
            italic=True,
            font_color=GREY,
            bg_color=LT,
            font_size=7,
            align="left",
            valign="vcenter",
            text_wrap=True,
            indent=1,
        ),
    )
    ws.set_row(r, 26)
    r += 1
    unres = M.get("unresolved_cusips") or []
    errs = [e["ticker"] for e in M.get("enrich_errors", [])]
    flags = (f"  ·  unresolved CUSIPs: {len(unres)}" if unres else "") + (
        f"  ·  no market data: {','.join(errs)}" if errs else ""
    )
    mc(
        r,
        0,
        NC - 1,
        f"Generated {M['generated'][:16].replace('T', ' ')} UTC  ·  CIK {M['cik']}  ·  accession {M['latest_accession']}{flags}",
        fnb(font_color=GREY, font_size=7, align="left", valign="vcenter", indent=1),
    )

    ws.freeze_panes(data_start, 0)
    ws.set_landscape()
    ws.set_paper(8)
    ws.fit_to_pages(1, 1)
    ws.center_horizontally()
    ws.set_margins(0.2, 0.2, 0.25, 0.25)
    ws.print_area(0, 0, r, NC - 1)
    wb.close()
    return out_path
