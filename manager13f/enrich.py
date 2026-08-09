"""Market-data enrichment via yfinance: technicals + analyst consensus, keyed by ticker.

Enrichment is per underlying ticker, so a Put/Call/Common on the same name shares it.
Fail-loud at the batch level: per-ticker failures are recorded in `errors` (so one dead
ticker can't silently shrink coverage), but a total wipeout raises.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import yfinance as yf

REC = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}


def _rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _atr_pct(h: pd.DataFrame, n: int = 14) -> float:
    pc = h["Close"].shift(1)
    tr = pd.concat([h["High"] - h["Low"], (h["High"] - pc).abs(), (h["Low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1] / h["Close"].iloc[-1] * 100)


def _pct(a, b):
    return None if (not b or a is None) else (a / b - 1) * 100


def benchmark_ytd(symbol: str = "SPY") -> float:
    h = yf.Ticker(symbol).history(period="1y", auto_adjust=True)
    base = h.loc[h.index >= f"{h.index[-1].year}-01-01", "Close"].iloc[0]
    return (h["Close"].iloc[-1] / base - 1) * 100


def historical_closes(tickers: list[str], start: str, pause: float = 0.3) -> tuple[dict, list[dict]]:
    """Return ({ticker: DataFrame[Close, AdjClose]}, errors) of daily prices from `start`.

    Powers the deep-mode multi-quarter performance attribution: a quarter-end close
    lookup (`raw Close`, matched against the 13F implied price as a data-quality guard)
    plus a split/dividend-adjusted close (`AdjClose`, for honest holding-period returns).
    Fail-loud at the batch level — per-ticker gaps are recorded so coverage can't shrink
    silently, but a total wipeout raises.
    """
    out: dict[str, pd.DataFrame] = {}
    errors: list[dict] = []
    for tk in sorted(set(tickers)):
        try:
            h = yf.Ticker(tk).history(start=start, auto_adjust=False)
            if h.empty or "Close" not in h:
                raise RuntimeError("no history")
            df = pd.DataFrame({"Close": h["Close"], "AdjClose": h.get("Adj Close", h["Close"])}).dropna(
                how="all"
            )
            if df.empty:
                raise RuntimeError("no usable closes")
            df.index = pd.DatetimeIndex(df.index).tz_localize(None)  # tz-naive for .asof lookups
            out[tk] = df
            time.sleep(pause)
        except Exception as e:  # record, don't swallow — coverage gaps must be visible
            errors.append({"ticker": tk, "error": str(e)})
    if tickers and not out:
        raise RuntimeError(
            f"historical_closes produced 0 series for {len(tickers)} tickers (systemic failure)"
        )
    return out, errors


def enrich_tickers(tickers: list[str], pause: float = 0.4) -> tuple[dict, list[dict]]:
    """Return ({ticker: metrics}, errors). Metrics computed from 1y daily history + .info."""
    out: dict[str, dict] = {}
    errors: list[dict] = []
    for tk in sorted(set(tickers)):
        try:
            t = yf.Ticker(tk)
            info = t.info or {}
            hist = t.history(period="1y", auto_adjust=True)
            if len(hist) < 20:
                raise RuntimeError(f"only {len(hist)} bars")
            close = hist["Close"]
            px = float(close.iloc[-1])
            ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
            ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
            ytd_base = close.loc[close.index >= f"{close.index[-1].year}-01-01"]
            rm = info.get("recommendationMean")
            tgt = info.get("targetMeanPrice")
            out[tk] = {
                "resolved_name": info.get("shortName") or info.get("longName"),
                "price": px,
                "ma50": ma50,
                "ma200": ma200,
                "pct_vs_ma200": _pct(px, ma200),
                "hi52": float(close.max()),
                "lo52": float(close.min()),
                "pct_from_hi": _pct(px, float(close.max())),
                "rsi14": _rsi(close),
                "atr_pct": _atr_pct(hist),
                "ytd": (px / ytd_base.iloc[0] - 1) * 100 if len(ytd_base) else None,
                "r3m": _pct(px, float(close.iloc[-66])) if len(close) > 66 else None,
                "trend": ("Golden" if (ma200 and ma50 and ma50 > ma200) else "Death") if ma200 else None,
                "rating": (REC.get(round(rm)) if rm else None)
                or (info.get("recommendationKey", "").replace("_", " ").title() or None),
                "rec_mean": rm,
                "n_analysts": info.get("numberOfAnalystOpinions"),
                "target_mean": tgt,
                "target_high": info.get("targetHighPrice"),
                "target_low": info.get("targetLowPrice"),
                "upside": _pct(tgt, px),
                "mktcap": info.get("marketCap"),
                "pe": info.get("trailingPE"),
                "fpe": info.get("forwardPE"),
                "beta": info.get("beta"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
            }
            time.sleep(pause)
        except Exception as e:  # record, don't swallow — coverage gaps must be visible
            errors.append({"ticker": tk, "error": str(e)})
    if tickers and not out:
        raise RuntimeError(
            f"yfinance enrichment produced 0 rows for {len(tickers)} tickers (systemic failure)"
        )
    return out, errors
