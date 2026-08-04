# 0005 — NSE paper capital kept at ₹50,000, with structural accommodations

## Status
Accepted (explicit user decision, overriding the recommendation raised in
review)

## Context
Post-SEBI Nov 2024 rules, NSE index derivative lot sizes were raised so
contract value sits at ₹15–20 lakh (NIFTY: 25 → 75). One ATM NIFTY lot at
₹50,000 capital risks roughly 7% of the account on a single trade against
a 1–2% professional norm; NSE Stock Options often cannot be sized at all
without exceeding the account. Full analysis: docs/SPEC_REVIEW.md §A2.

This was raised as a concern before implementation began. The user
reaffirmed ₹50,000 as specified. Per this project's working agreement,
that reaffirmation is the decision — it is implemented in full, not
re-litigated at each subsequent phase.

## Decision
NSE segments run at ₹50,000 as SPEC.md specifies. To make that operate as
well as it can rather than simply failing every risk check:

- Per-segment risk config is explicit and elevated for NSE (`base_risk_pct`
  7–8%, `hard_ceiling_pct` 25–35%) versus US (1.5% / 3%) — see
  ARCHITECTURE.md §11. The numbers are visible in config, not hidden.
- Every trade record carries the risk parameters in force, and both the
  dashboard and the export bundle show a segment's risk profile alongside
  its returns, so NSE and US performance are never compared as if achieved
  under the same risk.
- The Master Dashboard's cross-segment comparison defaults to
  risk-adjusted figures for this reason.
- An affordability constraint (`premium × lot_size ≤ max_premium_pct ×
  equity`) is promoted from SPEC.md's "capital efficiency" preference to a
  hard pre-trade gate, and the NSE Stock watchlist is pre-filtered to
  underlyings that can clear it at all.
- NSE's promotion gate (ARCHITECTURE.md §10, Track B) weighs a bootstrap
  confidence interval over raw trade count, since ₹50,000 at 1–2
  concurrent positions will not reach the 100-trade sample the US segments
  can.

## Consequences
- NSE Stock Options will trade infrequently; NSE Index will hold 1–2
  positions at a time. Both will accumulate a smaller sample than the US
  segments.
- Revisit if NSE Stock Options produces fewer than 5 trades/month for two
  consecutive months (ARCHITECTURE.md §20) — at that point the dataset the
  whole platform exists to produce is too thin on that segment to be
  useful, independent of strategy quality.
