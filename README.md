# manager-13f

## Specimen

[Download the cached Duquesne Family Office (Stan Druckenmiller) 13F intelligence sheet](examples/duquesne/duquesne_family_office_llc_2026-03-31.xlsx). It is a point-in-time specimen for the 2026-03-31 reporting period, filed 2026-05-15. The workbook is derived from public filing data; it is not a current portfolio view or a trading recommendation.

## The problem

A Form 13F is a delayed quarter-end disclosure, not a trade blotter. Its rows are noisy: issuer identity is CUSIP-based, some values are reported in thousands, and common stock, calls, and puts can coexist for one issuer. Treating every row as an ordinary long position can invert the reading of an options book.

## Design

The package resolves a filer, fetches the latest and prior 13F, parses the information tables with `(CUSIP, put/call)` as the position key, resolves CUSIPs, enriches resolvable tickers, calculates book analytics, and renders one worksheet.

Verification gates are explicit:

- Unresolved CUSIPs remain in the output; they are not discarded.
- Common, call, and put sleeves remain separate. Gross book weight and net notional are both shown.
- The parser infers a $1 versus $1,000 filing scale from median implied common-stock prices. It does not apply a blanket `×1000` rule just because a filing is small. When the implied-price signal and the fallback disagree, it logs the disagreement.
- Workbook generation has a structural merge check, and can perform an Excel clean-open check on macOS.

Live runs use SEC EDGAR and public market-data endpoints. Set `SEC_USER_AGENT` to a contact-bearing value before a live EDGAR fetch. Optional `OPENFIGI_API_KEY` increases CUSIP-resolution capacity but is not required.

## What refuses to ship

The tool raises on failed network or XML work rather than fabricating a filing. A blank resolver result is represented as unresolved. A failed enrichment is recorded per ticker; a systemic enrichment wipeout raises. The deep-history path marks a filing as anomalous instead of rescaling when there is no consistent power-of-ten explanation.

No 13F can establish trade dates, cost basis, delta-adjusted exposure, the long/short sign of an option contract, or holdings outside the form's reporting scope.

## Test coverage

The suite contains 33 offline tests: pure sleeve and QoQ analytics, value-scale inference, deep-history transforms, workbook structure, the clean-open adapter, and a committed cached-Duquesne fixture that builds a workbook without credentials or network access. It does not test live SEC, OpenFIGI, or market-data responses; those are deliberately excluded from the offline suite rather than silently mocked.

## Quickstart

```bash
uv sync
uv run pytest
uv run python examples/build_cached_duquesne_demo.py
```

The final command builds `output/duquesne_cached_demo.xlsx` from a committed cached rendering input. It uses zero keys and makes zero network calls.

For a live filing fetch after setting an appropriate SEC user agent:

```bash
SEC_USER_AGENT='your-name your-contact@example.com' uv run manager13f --cik 0001649339
```

Developed March–August 2026; git history squashed for privacy.

## License

MIT. See [LICENSE](LICENSE).
