"""The backtest runner (ARCHITECTURE.md §13): "same detectors, same
confluence scorer, same feature registry, same `as_of` discipline. Only
the clock and data source change." Walks a segment's benchmark-aligned
underlying/index bar history bar-by-bar, building a `FeatureContext` with
`chain=[]` at each step (Track A has no option chain — ADR/SPEC_REVIEW
A3), and resolves any signal against the underlying's own SUBSEQUENT bars
via `resolve.resolve_forward_outcome`.

Loads its whole bar history for the range up front (one query per
underlying/benchmark, not one per bar) — a backtest steps through
thousands of bars where the live engine steps through one tick; O(bars)
DB round trips the way `orchestrator.py` does per-tick would make this
unusably slow.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.backtest import resolve
from kairodex.backtest.types import BacktestSignal, ForwardOutcome
from kairodex.core.enums import Market, Segment, Side
from kairodex.data.types import Bar, Timeframe
from kairodex.features import loader as feature_loader
from kairodex.features import registry as feature_registry
from kairodex.features.types import FeatureContext
from kairodex.store.models import Instrument, UnderlyingBar
from kairodex.strategy.protocol import Strategy
from kairodex.strategy.scorer import ConfluenceScorer
from kairodex.strategy.types import MarketContext

_DEFAULT_LOOKBACK_BARS = 60  # >= volatility_regime's 50-bar long window, the largest of any
# bars-only feature this pipeline computes — see features/compute/volatility.py.
_DEFAULT_BAR_DAYS = 1.0  # daily bars (Timeframe.ONE_DAY) — this run_backtest's own default


async def _load_bars(
    session: AsyncSession,
    instrument_id: int,
    timeframe: Timeframe,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[Bar]:
    rows = await session.scalars(
        select(UnderlyingBar)
        .where(
            UnderlyingBar.instrument_id == instrument_id,
            UnderlyingBar.timeframe == timeframe.value,
            UnderlyingBar.ts >= start,
            UnderlyingBar.ts <= end,
        )
        .order_by(UnderlyingBar.ts)
    )
    return [
        Bar(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
        for r in rows
    ]


def _lead_time_pct(
    direction: Side, window: list[Bar], atr: float, outcome: ForwardOutcome, reference_bars: int
) -> float | None:
    """Fraction of the [prior + forward] move that came after the signal
    (SPEC_REVIEW C10, "Early Trade Detection," made measurable) — `prior`
    looks back `reference_bars` (capped by however much history the
    window actually has) from entry, in the SAME direction as the signal,
    clamped to >= 0 (a whipsaw against the signal contributes nothing to
    "how much of the move already happened," not a negative amount).
    `forward` is the outcome's own `mfe_atr` (already >= 0 by
    construction). `None` when neither side has anything to compare."""
    k = min(reference_bars, len(window) - 1)
    if k <= 0:
        return None
    sign = 1.0 if direction is Side.BUY else -1.0
    entry_price = float(window[-1].close)
    prior_price = float(window[-1 - k].close)
    prior_atr = max(0.0, sign * (entry_price - prior_price) / atr)
    forward_atr = max(0.0, outcome.mfe_atr)
    total = prior_atr + forward_atr
    if total <= 0:
        return None
    return forward_atr / total


async def run_backtest(
    session: AsyncSession,
    *,
    segment: Segment,
    underlying: Instrument,
    strategy: Strategy,
    scorer: ConfluenceScorer,
    start: datetime.datetime,
    end: datetime.datetime,
    timeframe: Timeframe = Timeframe.ONE_DAY,
    lookback_bars: int = _DEFAULT_LOOKBACK_BARS,
    bar_days: float = _DEFAULT_BAR_DAYS,
    stop_atr_mult: float = resolve.DEFAULT_STOP_ATR_MULT,
    target_atr_mult: float = resolve.DEFAULT_TARGET_ATR_MULT,
    max_holding_bars: int = resolve.DEFAULT_MAX_HOLDING_BARS,
) -> list[BacktestSignal]:
    """One underlying's full Track A run over `[start, end]`. Returns
    every resolved signal in-memory — `backtest_runs` stores an aggregate
    `metrics` JSONB summary (ARCHITECTURE.md §5.4's own schema), not a row
    per signal; there's no shared per-signal table this belongs in
    without conflating backtest and live/shadow `signals` rows, which
    carry no `run_id` to distinguish them (unlike `trades`)."""
    bars = await _load_bars(session, underlying.instrument_id, timeframe, start, end)
    if len(bars) < lookback_bars + 1:
        return []

    benchmark_symbol = feature_loader.benchmark_symbol(segment)
    exchange = "NSE" if segment.market is Market.NSE else "US"
    benchmark = await session.scalar(
        select(Instrument).where(
            Instrument.exchange == exchange, Instrument.symbol == benchmark_symbol
        )
    )
    index_by_ts: dict[datetime.datetime, Bar] = {}
    if benchmark is not None:
        index_bars_all = await _load_bars(session, benchmark.instrument_id, timeframe, start, end)
        index_by_ts = {b.ts: b for b in index_bars_all}

    signals: list[BacktestSignal] = []
    for i in range(lookback_bars, len(bars)):
        window = bars[i - lookback_bars : i + 1]  # bar i itself is the as_of point
        as_of = window[-1].ts
        index_window = [index_by_ts[b.ts] for b in window if b.ts in index_by_ts]

        feature_ctx = FeatureContext(
            as_of=as_of,
            segment=segment,
            underlying_bars=window,
            chain=[],
            prior_chain=[],
            index_bars=index_window,
        )
        values, _quality = feature_registry.compute_all(feature_ctx)
        market_ctx = MarketContext(feature_ctx=feature_ctx, features=values)
        evidence = strategy.evaluate(market_ctx)
        result = scorer.score(evidence)
        if result.direction is None:
            continue

        atr = values.get("atr")
        if atr is None or atr <= 0:
            continue

        entry_price = window[-1].close
        forward_bars = bars[i + 1 :]
        outcome = resolve.resolve_forward_outcome(
            result.direction,
            Decimal(str(entry_price)),
            Decimal(str(atr)),
            forward_bars,
            stop_atr_mult=stop_atr_mult,
            target_atr_mult=target_atr_mult,
            max_holding_bars=max_holding_bars,
        )
        if outcome is None:
            continue

        lead_time_pct = _lead_time_pct(result.direction, window, atr, outcome, max_holding_bars)

        signals.append(
            BacktestSignal(
                ts=as_of,
                segment=segment,
                underlying_symbol=underlying.symbol,
                direction=result.direction,
                confidence=result.confidence,
                entry_price=Decimal(str(entry_price)),
                atr_at_entry=Decimal(str(atr)),
                outcome=outcome,
                lead_time_pct=lead_time_pct,
                vol_regime=values.get("volatility_regime"),
            )
        )
    return signals
