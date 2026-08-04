# 0003 — Drop Backtrader

## Status
Accepted

## Context
SPEC.md's technology stack names Backtrader as the backtesting engine,
"extended with custom modules for options strategies, Greeks modeling,
multi-asset portfolios, institutional execution simulation." At the point
that extension list covers everything that actually matters, Backtrader
itself contributes only its bar-synchronous single-portfolio event loop.

ADR 0002 (backtest on underlying OHLCV) removes the original objection to
Backtrader — bar-based single-instrument backtesting is exactly its native
shape, and chain-based backtesting (which it genuinely can't do) is no
longer needed. This decision was re-examined, not assumed, once that
context changed.

## Decision
Still dropped. The reason that survives ADR 0002 is the one that mattered
most from the start: **Backtrader cannot run the live paper-trading path.**
Adopting it means every detector, the confluence scorer, and the risk gate
chain get expressed twice — once in Backtrader's `Cerebro`/`Strategy`/
`Indicator` idiom, once in this codebase's own idiom for live trading. The
golden replay-equivalence test that guards ADR 0001 becomes impossible to
write, because there would be two engines to keep in sync by hand instead
of one to verify.

`kairodex.engine`'s `ReplayClock` feeding bars instead of chains is now
~200–300 LOC — smaller than the Backtrader integration layer would have
been, and it is the one part of the system Backtrader would have owned.

## Consequences
- One engine, not two (see ADR 0001).
- No dependency on an effectively-unmaintained project.
- The backtest/live equivalence guarantee is testable at all, which it
  would not be with two separate implementations.
