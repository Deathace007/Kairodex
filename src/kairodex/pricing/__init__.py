"""Option pricing (ARCHITECTURE.md §8) — one module, both markets, so
cross-segment comparison is apples-to-apples:

  - black76:  European options priced off a synthetic forward (all NSE
              options). Sidesteps guessing a dividend yield/repo rate —
              the forward already encodes them (see `forward.py`).
  - bjerksund: American options (US equity/ETF options, incl. the
              SPY/QQQ/DIA/IWM us_index proxies per ADR 0007).
  - iv_solve: Brent's method, model-agnostic, given any price(sigma) fn.
  - forward:  put-call parity forward derivation.
  - greeks:   central finite-difference Greeks for pricers with no
              closed-form derivative (bjerksund; black76 has its own
              analytic greeks()).

Vendor-supplied Greeks (already flowing into `option_quotes` from P1) stay
a cross-check field, never the source of truth — see SPEC_REVIEW.md's
Black-76/Bjerksund decision.
"""
