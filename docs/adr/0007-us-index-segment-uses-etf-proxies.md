# 0007 — US Index segment trades SPY/QQQ/DIA/IWM (ETF proxies), not SPX/NDX/RUT

## Status
Accepted — user's explicit product decision, overriding SPEC_REVIEW.md §B1.

## Context
SPEC_REVIEW.md §B1 resolved "US Index Options" is undefined" by treating
the segment as SPX/NDX/RUT specifically — CBOE-listed, European-style
(exercise only at expiration, no early-assignment risk), cash-settled
(no shares change hands) index options, taxed under IRS §1256 (60%
long-term / 40% short-term regardless of holding period). That decision
explicitly kept SPY/QQQ/IWM out of the segment because they're a different
product: American-style (early-assignment risk on ITM legs), physically
settled, ordinary short-term tax treatment.

During P1 live verification (2026-08-04), this ran into a vendor wall:
LSE's entire options catalog (3,186 optionable underlyings, confirmed by
enumerating it directly) carries no SPX/NDX/RUT — `list_expiries()` for
all three returns zero contracts with no error, it's simply not offered.
CBOE index-option data is commonly licensed and distributed separately
from standard equity/ETF options, and LSE doesn't carry it.

Presented with this, the user chose: **the US Index segment trades index-
tracking ETF options instead** — specifically S&P 500 (SPY), NASDAQ 100
(QQQ), Dow Jones (DIA), and Russell 2000 (IWM), all four confirmed present
in LSE's live catalog. This is a deliberate, informed override of B1's
reasoning, not an oversight — the user was told the mechanical differences
(European/cash-settled vs. American/physically-settled, §1256 vs. ordinary
tax treatment) before deciding.

## Decision
- `US_INDEX` segment watchlist = `SPY`, `QQQ`, `DIA`, `IWM` (`config/watchlist.yaml`).
- These are real ETF shares, not index values — `kairodex.data.lse.client.LSEClient.instruments()`
  now always yields `InstrumentKind.UNDERLYING` for every LSE underlying
  (there are no true `INDEX`-kind instruments reachable through this
  vendor at all; the old SPX/NDX/RUT-keyed `is_index` branch was dead code
  once those symbols don't exist in the catalog).
- Segment assignment (`US_INDEX` vs `US_STOCK`) is a **product/watchlist
  classification**, not a fact derivable from the vendor's own instrument
  kind — it comes from which watchlist an underlying is seeded into
  (`kairodex ingest sync-watchlist`), consumed by the recorder
  (`kairodex/data/recorder.py`) when it stores chain snapshots. `SPY`/`QQQ`
  cannot be a member of both `us_stock` and `us_index` at once: the same
  option-contract rows would have their `segment` column overwritten
  non-deterministically depending on watchlist iteration order, corrupting
  the segment isolation ARCHITECTURE.md treats as load-bearing. They were
  removed from `us_stock`.

## Consequences
- Every downstream consumer of this segment (pricing/Greeks in P2, risk
  gates and capital sizing in P3, cost models) must use the **American,
  physically-settled** pricing and cost model — the same one `US_STOCK`
  already uses — not the European/cash-settled model B1 originally
  specified for true index options. There is currently only one US pricing
  path in this codebase, so this is consistent by construction, but it's
  worth remembering *why*: it's not that the two products got merged, it's
  that this segment's actual instruments changed.
- If a future vendor (or a paid LSE tier) does carry real SPX/NDX/RUT data,
  switching back is a watchlist + segment-classification change, not a
  schema change — `Segment.US_INDEX` and the option-quotes pipeline are
  unchanged either way.
- SPEC_REVIEW.md §B1 is left in place as the original reasoning (not
  deleted, same discipline as ADR 0006) — this ADR is the record of what
  actually shipped and why it diverges.
