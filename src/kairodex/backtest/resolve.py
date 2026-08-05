"""Pure forward-outcome resolution (ARCHITECTURE.md §13's Track A) — no
DB, no I/O, same split as `kairodex.execution.fills`: "given a signal
right now, what would happen." Track A has no real position to manage
(no option, no fill, no fees — that's Track B, already live via P3), so
"what happens" here is a synthetic stop/target walk over the underlying's
own subsequent bars, in ATR units — the doc's own unit for expectancy.

Ambiguous same-bar stop+target hits resolve to the stop (conservative:
daily/hourly bars can't tell us which happened first intrabar, and
understating an edge is safer than overstating one for a promotion gate).
"""

from __future__ import annotations

from decimal import Decimal

from kairodex.backtest.types import ForwardOutcome
from kairodex.core.enums import Side
from kairodex.data.types import Bar

_DEFAULT_STOP_ATR_MULT = 1.0
_DEFAULT_TARGET_ATR_MULT = 2.0  # 2:1 reward:risk, matching monitor.py's own R-multiple convention
_DEFAULT_MAX_HOLDING_BARS = 10


def resolve_forward_outcome(
    direction: Side,
    entry_price: Decimal,
    atr_at_entry: Decimal,
    forward_bars: list[Bar],
    *,
    stop_atr_mult: float = _DEFAULT_STOP_ATR_MULT,
    target_atr_mult: float = _DEFAULT_TARGET_ATR_MULT,
    max_holding_bars: int = _DEFAULT_MAX_HOLDING_BARS,
) -> ForwardOutcome | None:
    """`None` when there's nothing to resolve against (no forward bars,
    or ATR isn't a usable risk unit yet) — the caller should skip
    persisting a signal it can't score, not synthesize a fake one."""
    if not forward_bars or atr_at_entry <= 0:
        return None

    sign = Decimal(1) if direction is Side.BUY else Decimal(-1)
    stop_price = entry_price - sign * atr_at_entry * Decimal(str(stop_atr_mult))
    target_price = entry_price + sign * atr_at_entry * Decimal(str(target_atr_mult))

    bars = forward_bars[:max_holding_bars]
    mfe = Decimal(0)
    mae = Decimal(0)
    for i, bar in enumerate(bars):
        favorable_extreme = bar.high if direction is Side.BUY else bar.low
        adverse_extreme = bar.low if direction is Side.BUY else bar.high
        favorable_this_bar = sign * (favorable_extreme - entry_price)
        adverse_this_bar = -sign * (adverse_extreme - entry_price)
        mfe = max(mfe, favorable_this_bar)
        mae = max(mae, adverse_this_bar)

        hit_stop = bar.low <= stop_price if direction is Side.BUY else bar.high >= stop_price
        hit_target = (
            bar.high >= target_price if direction is Side.BUY else bar.low <= target_price
        )
        if hit_stop:  # stop wins on an ambiguous same-bar hit — see module docstring
            return _outcome("STOP", stop_price, i + 1, mfe, mae, atr_at_entry, sign, entry_price)
        if hit_target:
            return _outcome(
                "TARGET", target_price, i + 1, mfe, mae, atr_at_entry, sign, entry_price
            )

    last_close = bars[-1].close
    return _outcome("TIME", last_close, len(bars), mfe, mae, atr_at_entry, sign, entry_price)


def _outcome(
    reason: str,
    exit_price: Decimal,
    bars_held: int,
    mfe: Decimal,
    mae: Decimal,
    atr: Decimal,
    sign: Decimal,
    entry_price: Decimal,
) -> ForwardOutcome:
    atr_f = float(atr)
    signed_return = sign * (exit_price - entry_price)
    return ForwardOutcome(
        exit_reason=reason,
        exit_price=exit_price,
        bars_held=bars_held,
        mfe=mfe,
        mae=mae,
        mfe_atr=float(mfe) / atr_f,
        mae_atr=float(mae) / atr_f,
        return_atr=float(signed_return) / atr_f,
    )


