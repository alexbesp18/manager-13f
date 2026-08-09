"""CUSIP -> ticker resolution via OpenFIGI, with a compounding local cache.

OpenFIGI free tier (no key) allows ~25 jobs / 6s; with an OPENFIGI_API_KEY env
var the limit is far higher. Unresolved CUSIPs are returned in `unresolved` so the
caller can fall back to a name match or the fleet's ticker-resolver agent — they are
never silently dropped.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

_DATA = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = _DATA / "cusip_ticker_cache.json"
ALIAS_PATH = _DATA / "cusip_aliases.json"  # curated tier: filled by the ticker-resolver agent
_OPENFIGI = "https://api.openfigi.com/v3/mapping"


def _load_aliases() -> dict[str, str]:
    return json.loads(ALIAS_PATH.read_text()) if ALIAS_PATH.exists() else {}


def _load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=0, sort_keys=True))


_US_EXCH = {"US", "UN", "UW", "UQ", "UA", "UR", "UV", "UP", "UF"}  # NYSE/Nasdaq/etc Bloomberg codes


def _openfigi_batch(cusips: list[str], us_only: bool) -> dict[str, str]:
    """Map a batch of CUSIPs (<=batch) to tickers, preferring a US-listed common/ETF line.
    us_only=True restricts the query to US exchanges (clean); False queries all listings
    (catches foreign-domiciled CINS like G/N/Q that nonetheless trade on a US exchange)."""
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("OPENFIGI_API_KEY"):
        headers["X-OPENFIGI-APIKEY"] = key
    extra = {"exchCode": "US"} if us_only else {}
    jobs = [{"idType": "ID_CUSIP", "idValue": c} | extra for c in cusips]
    r = requests.post(_OPENFIGI, headers=headers, data=json.dumps(jobs), timeout=30)
    if r.status_code == 429:
        time.sleep(6)  # rate limited — wait one window, retry once
        r = requests.post(_OPENFIGI, headers=headers, data=json.dumps(jobs), timeout=30)
    r.raise_for_status()
    out: dict[str, str] = {}
    for cusip, res in zip(cusips, r.json(), strict=True):
        data = res.get("data") or []
        if not data:
            continue
        # accept ONLY US-listed lines (reject foreign venues like Frankfurt "1B2"),
        # preferring common stock / ETF / ADR.
        us = sorted(
            (d for d in data if d.get("ticker") and d.get("exchCode") in _US_EXCH),
            key=lambda d: 0 if d.get("securityType2") in ("Common Stock", "ETP", "Depositary Receipt") else 1,
        )
        if us:
            out[cusip] = us[0]["ticker"].replace("/", "-").upper()
    return out


def resolve(cusips: list[str], names: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Resolve CUSIPs to tickers. Returns (mapping, unresolved). Cache-backed and batched."""
    cusips = sorted(set(cusips))
    cache = _load_cache()
    aliases = _load_aliases()  # curated tier wins over cache (hand/agent-verified)
    mapping = {c: aliases[c] for c in cusips if c in aliases}
    mapping.update({c: cache[c] for c in cusips if c in cache and c not in mapping})
    todo = [c for c in cusips if c not in mapping]
    has_key = "OPENFIGI_API_KEY" in os.environ
    bsize = 100 if has_key else 10  # free tier caps at 10 jobs/request

    def _pass(items, us_only):
        for i in range(0, len(items), bsize):
            got = _openfigi_batch(items[i : i + bsize], us_only=us_only)
            mapping.update(got)
            cache.update(got)
            if i + bsize < len(items):
                time.sleep(0.3 if has_key else 1.5)

    _pass(todo, us_only=True)  # pass 1: clean US-exchange query
    still = [c for c in todo if c not in mapping]
    if still:
        _pass(still, us_only=False)  # pass 2: all listings (foreign CINS that trade in the US)
    _save_cache(cache)
    unresolved = [c for c in cusips if c not in mapping]  # for name fallback / ticker-resolver agent
    return mapping, unresolved
