"""SEC EDGAR access: discover a manager's CIK, fetch the latest + prior 13F-HR,
and parse the information tables into option-aware holdings.

Fail-loud: every network/parse error propagates (no silent except/pass).
Parsed 13F values are normalized to whole USD. Some modern filings still emit
the traditional $000 value unit, so fetch_filing normalizes tiny filing totals
before analytics compute filing prices and mark-to-market moves. Put/Call lines
report the market value of the UNDERLYING shares (notional), not option premium.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import requests
from defusedxml.ElementTree import fromstring as xml_fromstring  # XXE/billion-laughs safe

# SEC asks automated clients to identify themselves. Set SEC_USER_AGENT to a
# contact-bearing value before running a live EDGAR fetch; the public default
# intentionally contains no personal contact information.
UA = os.environ.get("SEC_USER_AGENT", "manager13f/0.1 public research")
NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
MIN_EXPECTED_13F_TOTAL_USD = 100_000_000

log = logging.getLogger(__name__)


def _get(url: str, *, tries: int = 4) -> requests.Response:
    """GET with EDGAR's 10 req/s politeness + exponential backoff. Raises on final failure."""
    last = None
    for i in range(tries):
        try:
            r = _SESSION.get(url, timeout=30)
            if r.status_code == 200:
                time.sleep(0.12)
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
        except requests.RequestException as e:  # network blip — retry, then propagate
            last = e
        time.sleep(0.5 * (2**i))
    raise last  # fail loud


@dataclass
class Position:
    name: str
    cusip: str
    title_class: str
    value: int  # whole USD (notional for options)
    shares: int
    sh_type: str  # SH or PRN
    put_call: str  # "" (long), "PUT", or "CALL"

    @property
    def key(self) -> tuple[str, str]:
        # An issuer can appear as Common, Put, AND Call simultaneously — keep them distinct.
        return (self.cusip, self.put_call)


@dataclass
class Filing:
    cik: str
    accession: str
    period: str  # YYYY-MM-DD (report date / quarter end)
    filed: str
    positions: list[Position] = field(default_factory=list)

    @property
    def total_value(self) -> int:
        return sum(p.value for p in self.positions)


def discover_cik(name: str) -> tuple[str, str]:
    """Resolve a manager name to (cik10, official_name) via EDGAR company search.
    Raises if nothing 13F-filing matches."""
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&company={requests.utils.quote(name)}&type=13F-HR&dateb=&owner=include&count=10&output=atom"
    )
    root = xml_fromstring(_get(url).content)
    atom = "{http://www.w3.org/2005/Atom}"
    cik_el = root.find(f".//{atom}entry/{atom}content/{atom}cik") or root.find(f".//{atom}cik")
    name_el = root.find(f".//{atom}conformed-name") or root.find(f".//{atom}title")
    if cik_el is None:
        raise LookupError(f"No 13F-HR filer found for '{name}' on EDGAR")
    return cik_el.text.zfill(10), (name_el.text if name_el is not None else name)


def list_13f(cik10: str) -> tuple[str, list[dict]]:
    """Return (official_name, 13F-HR filings newest-first) from the submissions JSON."""
    cik = cik10.lstrip("0")
    data = _get(f"https://data.sec.gov/submissions/CIK{cik10}.json").json()
    official = data.get("name", cik10)
    r = data["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        if form == "13F-HR":  # exclude 13F-NT (notice, no holdings) and amendments by default
            out.append(
                {
                    "accession": r["accessionNumber"][i],
                    "period": r["reportDate"][i],
                    "filed": r["filingDate"][i],
                    "cik": cik,
                }
            )
    if not out:
        raise LookupError(f"CIK {cik10} has no 13F-HR holdings filings")
    return official, out


def _info_table_url(cik_nozero: str, accession: str) -> str:
    accn = accession.replace("-", "")
    idx = _get(f"https://www.sec.gov/Archives/edgar/data/{cik_nozero}/{accn}/").text
    # the holdings doc is the .xml that isn't primary_doc.xml
    for line in idx.split('href="'):
        href = line.split('"')[0]
        if href.endswith(".xml") and "primary_doc" not in href:
            return href if href.startswith("http") else f"https://www.sec.gov{href}"
    raise FileNotFoundError(f"No information-table XML in accession {accession}")


def parse_info_table(xml_bytes: bytes) -> list[Position]:
    root = xml_fromstring(xml_bytes)
    agg: dict[tuple[str, str], Position] = {}
    for it in root.findall(f"{NS}infoTable"):

        def t(tag: str, default: str = "", _it=it) -> str:
            el = _it.findtext(f"{NS}{tag}")
            return el.strip() if el else default

        sh_el = it.find(f"{NS}shrsOrPrnAmt")
        pc_el = it.find(f"{NS}putCall")
        pos = Position(
            name=t("nameOfIssuer"),
            cusip=t("cusip").upper(),
            title_class=t("titleOfClass"),
            value=int(t("value", "0")),
            shares=int((sh_el.findtext(f"{NS}sshPrnamt") or 0) if sh_el is not None else 0),
            # .strip(): some filers emit " SH " with whitespace; an unstripped value silently
            # breaks the sh_type=="SH" equality checks downstream (e.g. the scale-guard sample).
            sh_type=((sh_el.findtext(f"{NS}sshPrnamtType") or "SH").strip() if sh_el is not None else "SH"),
            put_call=(pc_el.text.strip().upper() if pc_el is not None and pc_el.text else ""),
        )
        if pos.key in agg:  # same issuer+class+side split across rows -> combine
            agg[pos.key].value += pos.value
            agg[pos.key].shares += pos.shares
        else:
            agg[pos.key] = pos
    return list(agg.values())


def _infer_scale(positions: list[Position]) -> int:
    """Multiplier (1 or 1000) to convert raw 13F values to whole USD.

    Pre-2023 filings report value in $1000s; 2023+ in whole dollars. The implied
    price (value / shares) of a common-stock holding is < $1 only in the $000
    case, so use the MEDIAN implied price across common-stock positions (robust
    to a single odd row). Fall back to the >= $100M AUM floor only when there is
    no common-stock signal (e.g. an all-options or all-bond filing).
    """
    implied = sorted(
        p.value / p.shares
        for p in positions
        if p.shares > 0 and p.value > 0 and p.sh_type == "SH" and not p.put_call
    )
    if implied:
        return 1000 if implied[len(implied) // 2] < 1 else 1
    total = sum(p.value for p in positions)
    return 1000 if 0 < total < MIN_EXPECTED_13F_TOTAL_USD else 1


def _normalize_value_units(positions: list[Position]) -> list[Position]:
    """Normalize raw information-table values to whole USD.

    Scale is inferred from the median implied price of common-stock holdings —
    robust, and unlike a bare AUM-floor proxy it will NOT 1000x a legitimately
    small whole-dollar book. The AUM floor is kept only as a cross-check: if the
    two signals disagree the implied-price wins and a warning is logged (the
    decision was previously silent).
    """
    mult = _infer_scale(positions)
    total_raw = sum(p.value for p in positions)
    floor_says_thousands = 0 < total_raw < MIN_EXPECTED_13F_TOTAL_USD
    if (mult == 1000) != floor_says_thousands:
        log.warning(
            "13F scale signals disagree: implied-price -> mult=%d but AUM-floor "
            "-> %s (raw total $%d) — using implied-price",
            mult,
            "thousands" if floor_says_thousands else "whole-USD",
            total_raw,
        )
    if mult == 1000:
        for p in positions:
            p.value *= 1000
    log.info(
        "13F scale: multiplier=%d (raw total $%d, %d positions)",
        mult,
        total_raw,
        len(positions),
    )
    return positions


def fetch_filing(cik_nozero: str, meta: dict) -> Filing:
    url = _info_table_url(cik_nozero, meta["accession"])
    positions = _normalize_value_units(parse_info_table(_get(url).content))
    return Filing(
        cik=cik_nozero,
        accession=meta["accession"],
        period=meta["period"],
        filed=meta["filed"],
        positions=positions,
    )


def latest_and_prior(name_or_cik: str) -> tuple[str, str, Filing, Filing | None]:
    """Resolve the manager and return (cik10, official_name, latest_filing, prior_filing_or_None)."""
    if name_or_cik.isdigit() or name_or_cik.lower().startswith("cik"):
        cik10 = "".join(c for c in name_or_cik if c.isdigit()).zfill(10)
    else:
        cik10, _ = discover_cik(name_or_cik)
    official, filings = list_13f(cik10)  # name from submissions JSON (reliable)
    cik_nozero = cik10.lstrip("0")
    latest = fetch_filing(cik_nozero, filings[0])
    prior = fetch_filing(cik_nozero, filings[1]) if len(filings) > 1 else None
    return cik10, official, latest, prior
