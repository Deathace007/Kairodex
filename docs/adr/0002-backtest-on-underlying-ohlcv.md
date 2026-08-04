# 0002 — Backtest on underlying OHLCV, not historical option chains

## Status
Accepted

## Context
SPEC.md's promotion gate ("only strategies demonstrating statistically
meaningful performance should be eligible for paper trading") implicitly
assumes historical option data is available to backtest against. It isn't,
symmetrically, across both markets:

- Upstox's NSE option history requires a paid "Plus" subscription
  (Expired Instruments API, launched May 2025) and even then returns
  OHLCV + OI only — no historical IV, Greeks, or bid/ask, ever.
- LSE's US option history is far richer (chains with Greeks, options-flow
  prints back to 2014), but building the backtest engine around option
  chains on one market and being unable to do so on the other defeats the
  point of a shared engine (ADR 0001).

Full analysis: docs/SPEC_REVIEW.md §A3.

## Decision
Backtesting (Track A) runs on underlying OHLCV, which both vendors provide
deeply and for free (Upstox candles v3; LSE US equities to 2003). It
answers a narrower, genuinely answerable question: does the algorithm read
market direction and timing correctly? Measured in market terms — hit
rate, MFE/MAE, expectancy in ATR units, signal lead time — not currency
P&L.

Option economics (does a correct directional call actually profit after
spread, theta, and IV crush) is validated separately in Track B: live
shadow mode against real option chains, with zero capital at risk.

A synthetic-option overlay prices a Black-76 option along the backtested
underlying path as a second pass, flagging strategies whose directional
edge is on moves too small or slow to beat theta — before they consume
weeks of shadow-mode time. This is an estimate, not a promotion gate; only
Track B's real fills decide that.

## Consequences
- No Upstox Plus subscription is required.
- Historical option-chain reconstruction (Greek back-solving from expired
  contract closes) is out of scope entirely — deleted, not deferred.
- Roughly a third of SPEC.md's Market Analysis Engine list (order flow,
  absorption, bid/ask imbalance) can only ever be validated in Track B —
  the feature registry marks these `backtestable=False` so the promotion
  pipeline routes them correctly.
- Promotion gates split into two sets (ARCHITECTURE.md §10): Track A gates
  on directional metrics: Track B gates on real option P&L.
