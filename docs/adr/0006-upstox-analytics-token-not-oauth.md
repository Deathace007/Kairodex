# 0006 — Upstox market-data auth uses the Analytics Token, not daily OAuth

## Status
Accepted — corrects a claim made during the architecture review.

## Context
docs/SPEC_REVIEW.md §C1 originally flagged Upstox's standard OAuth access
token — which expires daily at a fixed 3:30am IST regardless of issue
time, requiring an interactive browser login to renew — as "the #1
operational failure mode of the whole system," and ARCHITECTURE.md's P1
roadmap line and reliability table were written around building an OAuth
callback route and a pre-open token-validity job to manage that.

During implementation of the Upstox adapter (P0, task 8), the user
corrected this: Upstox separately offers an **Analytics Token** — a
long-lived (1 year), read-only bearer token generated manually once from
the Developer Apps dashboard, with no OAuth dance and no daily
re-authentication. It covers exactly what this platform's ingestion path
needs (market data, option chain, historical candles, websocket) and nothing
it must never have (it cannot place, modify, or cancel orders). This
matches SPEC.md's own instruction to use "the Upstox Analytics Long Term
API" — the daily-OAuth assumption was simply the wrong product.

## Decision
The Upstox adapter (`kairodex.data.upstox.auth.AnalyticsToken`) authenticates
with a static bearer token read from `UPSTOX_ACCESS_TOKEN`, generated
manually via Developer Apps → Analytics tab → Generate Token, with its
expiry date recorded in `UPSTOX_TOKEN_EXPIRES_AT`. There is no OAuth
authorize/token-exchange flow anywhere in this codebase for Upstox, and
there never needs to be one, because market data is the only thing this
platform ever asks Upstox for.

Upstox exposes no endpoint to query the token's remaining validity
programmatically, so expiry is tracked from the date read off the
dashboard at generation time; `is_expiring_soon()` exists for a monitoring
job (P1) to alert well before the ~1-year mark — an annual concern, not a
daily one.

## Consequences
- SPEC_REVIEW.md §C1 is corrected in place (not deleted — the daily-OAuth
  risk was real for the product I'd assumed; the correction is left
  visible so the reasoning trail stays honest) and ARCHITECTURE.md's P1
  roadmap line and reliability-table row are updated to match.
- The Analytics Token's inability to place orders is a second, free
  structural reinforcement of the paper-only guarantee on the NSE side
  (ARCHITECTURE.md §11): this credential physically cannot touch a live
  order even under a configuration mistake upstream.
- One fewer subsystem to build: no OAuth callback route, no interactive
  login flow, no daily-reauth alerting.
