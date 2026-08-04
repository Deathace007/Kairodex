# 0001 — One engine, two clocks

## Status
Accepted

## Context
The platform needs both a live paper-trading engine and a backtest/replay
engine. Building them as separate codebases (as most trading systems end up
doing, often via a backtesting framework like Backtrader) creates two
implementations of every detector, scorer, risk gate, and fill model. They
drift. The single most common way platforms like this fail is silent
backtest/live divergence — a strategy that "worked" in backtest and loses
in paper because the two code paths didn't actually agree on what they were
measuring.

## Decision
There is exactly one orchestrator (`kairodex.engine`). It is parameterized by a
`Clock` port (`LiveClock` for paper trading, `ReplayClock` for backtest —
the latter added in P4) and a `MarketDataProvider`. Every detector, the
confluence scorer, every risk gate, and the fill simulator run identically
regardless of which clock drives them.

A golden test (`tests/replay/`) records one live session and asserts that
replaying it through `ReplayClock` reproduces the exact same trades. This
is the only test in the suite that directly guards this decision, and it is
the most valuable one.

## Consequences
- No third-party backtesting framework fits this shape (see ADR 0003).
- The replay loop is small (~200–300 LOC) because it only has to drive the
  clock — everything else already exists for the live path.
- Any change to a detector or the risk chain automatically applies to both
  live and backtest; there is no second place to remember to update.
