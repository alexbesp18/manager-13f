# Manager 13F

## What this is

Public, offline-testable Form 13F intelligence-sheet generator. It preserves
unresolved identities and ambiguous filing-scale evidence instead of guessing.

## How to run

Run `uv sync`, `uv run pytest`, and `uv run python
examples/build_cached_duquesne_demo.py`. The demo uses committed cached inputs and
does not require credentials or a network.

## What's live

Live SEC/market-data fetches are manual and require `SEC_USER_AGENT`; no
scheduled runtime is declared here.

## Do not touch

Do not present a filing as current holdings or trading advice, or erase
unresolved CUSIPs/options distinctions to make an output look complete.
