"""Exit-ladder replay sweep (docs/reports/2026-09-16-why-only-one-winner.html).

Answers two coupled questions that the 2026-09-16 session raised and that
no amount of live watching can settle quickly, because at ~9 trades a day
the live book grows far slower than the number of knobs:

  1. `scratch_exit_after_minutes` — 15 cuts five of nine trades on 09-16,
     and the only winner cleared the +3% test at minute 14, with 60
     seconds to spare. Is 15 too tight now?
  2. The runner guard — with real lot sizes and `max_concurrent: 2` every
     position is 1 lot, so `PARTIAL_EXIT_R2` sells 100% and no trade ever
     keeps a runner. `monitor.r_multiple_partial_exit_check` documents a
     `qty_lots < 2` guard that was written on 2026-08-14 and REVERTED,
     because abstaining handed 1-lot positions to the stop and the trail.
     The breakeven floor did not exist then. Does it change the answer?

They are swept together because they interact: a longer scratch window
only pays if what it keeps alive can still run, and a runner only pays if
it is not scratched first.

METHOD, and its limits, stated plainly:

  * The ladder is not re-implemented. `monitor.evaluate_exits` is called
    directly, so the replay cannot drift from what the engine does. The
    runner guard is simulated by withholding `r_multiple_targets` when
    the remaining size is 1 lot, which is exactly what the reverted guard
    did.
  * Prices come from the leg's own `option_quotes`, resampled to 1-minute
    last-LTP, from entry to the session close of the entry day. NOT from
    `position_marks`: those stop at the original exit, so a variant that
    holds longer than the live rule did would have no prices to hold
    over. That is the whole point of the sweep, so the wider source is
    mandatory. `mark = ltp` matches `orchestrator.run_exit_tick`.
  * 1-minute granularity. The live loop checks exits every ~30s since the
    09-16 throughput fix and far less often before it, so this is
    comparable and slightly conservative: a within-minute spike is not
    tradable here.
  * Exits fill at mark x (1 - EXIT_SLIPPAGE), entries at the recorded
    `avg_entry`, and real NSE costs via `execution.costs.compute_nse_costs`.
  * EVERY trade is replayed at 1 lot of its REAL lot size, from
    `instrument_specs` as of the trade date. Historical rows were sized
    on a hardcoded 25-unit lot (fixed 2026-09-15), so their recorded
    rupee P&L is scale-wrong per name and must never be pooled. Re-sizing
    properly would need the whole gate chain and account state; 1 lot is
    both the honest simplification and what the live engine now does
    anyway.
  * Consequently the rupee totals here are NOT comparable with the
    recorded book. Variants are comparable with EACH OTHER, which is the
    only comparison being asked for.

Run it on the VM (never locally — the quotes hypertable is ~90M rows):

    uv run python -m kairodex.backtest.exit_replay
"""

from __future__ import annotations

import asyncio
import datetime
import statistics
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text

from kairodex.core.enums import Segment, Side
from kairodex.core.sessions import session_seconds_between, session_window_utc
from kairodex.engine.monitor import Position, evaluate_exits
from kairodex.execution.costs import compute_nse_costs
from kairodex.store.base import get_sessionmaker

# Trades whose outcome was decided by a defect, not by the ruleset —
# `analytics.loader.NON_ATTRIBUTABLE_TRADES`. Kept in sync by hand; the
# sweep would otherwise score the EOD-exit bug and the holiday session.
NON_ATTRIBUTABLE = (87, 95, 101, 194, 196, 218, 219, 220)

# The live nse_stock ruleset the sweep holds fixed. Only the two swept
# knobs vary; everything else is read from the same constants the engine
# uses so a config change cannot silently desync the replay.
STOP_LOSS_PCT = 0.20
PROFIT_TARGET_PCT = 1.0
R_TARGETS = (2.0,)
BREAKEVEN_TRIGGER_PCT = 0.10
BREAKEVEN_FLOOR_PCT = 0.0
SCRATCH_MIN_MFE_PCT = 0.03
MAX_HOLDING_SESSIONS = 3.0
PARTIAL_EXIT_FRACTION = 0.5

# Modelled cost of crossing the spread to get out. The 2026-09-15 replay
# used mark x 0.991 and this keeps that constant so the two are readable
# against each other.
EXIT_SLIPPAGE = Decimal("0.009")


@dataclass(frozen=True, slots=True)
class Variant:
    scratch_minutes: int | None  # None = scratch rule off
    runner_guard: bool  # True = a 1-lot position skips the R rung

    @property
    def label(self) -> str:
        s = "off" if self.scratch_minutes is None else f"{self.scratch_minutes}m"
        return f"scratch={s:>3} runner={'on ' if self.runner_guard else 'off'}"


@dataclass(frozen=True, slots=True)
class TradeInput:
    trade_id: int
    underlying_symbol: str
    opened_at: datetime.datetime
    session_date: datetime.date
    avg_entry: Decimal
    lot_size: int
    expiry: datetime.date | None
    path: tuple[tuple[datetime.datetime, Decimal], ...]


@dataclass(slots=True)
class Outcome:
    trade_id: int
    session_date: datetime.date
    net: Decimal = Decimal(0)
    r: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _fees(premium: Decimal, side: Side) -> Decimal:
    c = compute_nse_costs(side, premium)
    return c.brokerage + c.regulatory_fees + c.taxes


def replay(t: TradeInput, v: Variant) -> Outcome:
    """Walk the trade's own price path under one variant's ruleset."""
    entry = t.avg_entry
    initial_stop = entry * Decimal(str(1 - STOP_LOSS_PCT))
    stop_price = initial_stop
    hwm = entry
    qty = 1
    taken: frozenset[float] = frozenset()
    reasons: list[str] = []

    gross = Decimal(0)
    cost = _fees(entry * t.lot_size, Side.BUY)
    for ts, mark in t.path:
        if mark <= 0:
            continue
        hwm = max(hwm, mark)
        # The guard: withhold the rung while only one lot remains. This is
        # precisely what the reverted `qty_lots < 2: return None` did.
        targets = () if (v.runner_guard and qty < 2) else R_TARGETS
        pos = Position(
            trade_id=t.trade_id,
            segment=Segment.NSE_STOCK,
            instrument_id=0,
            underlying_symbol=t.underlying_symbol,
            side=Side.BUY,
            qty_lots=qty,
            lot_size=t.lot_size,
            avg_entry=entry,
            opened_at=t.opened_at,
            stop_price=stop_price,
            initial_stop_price=initial_stop,
            current_mark=mark,
            high_water_mark_price=hwm,
            profit_target=entry * Decimal(str(1 + PROFIT_TARGET_PCT)),
            r_multiple_targets=targets,
            partial_exits_taken=taken,
            max_holding_secs=int(MAX_HOLDING_SESSIONS * 6.25 * 3600),
            held_session_secs=session_seconds_between(Segment.NSE_STOCK.market, t.opened_at, ts),
            expiry=t.expiry,
            scratch_after_secs=(None if v.scratch_minutes is None else v.scratch_minutes * 60),
            scratch_min_mfe_pct=SCRATCH_MIN_MFE_PCT,
            breakeven_trigger_pct=BREAKEVEN_TRIGGER_PCT,
            breakeven_floor_pct=BREAKEVEN_FLOOR_PCT,
        )
        d = evaluate_exits(pos, ts, partial_exit_fraction=PARTIAL_EXIT_FRACTION)
        if d is None:
            continue
        if d.qty_lots == 0:  # STOP_RATCHET — bookkeeping only
            if d.new_stop_price is not None:
                stop_price = d.new_stop_price
            continue
        fill = mark * (1 - EXIT_SLIPPAGE)
        gross += (fill - entry) * d.qty_lots * t.lot_size
        cost += _fees(fill * d.qty_lots * t.lot_size, Side.SELL)
        reasons.append(d.reason)
        if d.reason.startswith("PARTIAL_EXIT_R"):
            taken = taken | {float(d.reason.removeprefix("PARTIAL_EXIT_R"))}
        qty -= d.qty_lots
        if qty <= 0:
            break

    if qty > 0:  # never triggered a rule — close at the last price we have
        _, mark = t.path[-1]
        fill = mark * (1 - EXIT_SLIPPAGE)
        gross += (fill - entry) * qty * t.lot_size
        cost += _fees(fill * qty * t.lot_size, Side.SELL)
        reasons.append("EOD_FORCED")

    net = gross - cost
    risk = (entry - initial_stop) * t.lot_size
    return Outcome(
        trade_id=t.trade_id,
        session_date=t.session_date,
        net=net,
        r=float(net / risk) if risk > 0 else 0.0,
        reasons=tuple(reasons),
    )


_TRADES_SQL = text(
    """
    SELECT t.trade_id,
           t.instrument_id,
           u.symbol                                        AS usym,
           t.opened_at,
           (t.opened_at AT TIME ZONE 'Asia/Kolkata')::date  AS sdate,
           t.avg_entry,
           i.expiry,
           COALESCE(s.lot_size, t.lot_size)                AS lot_size
      FROM trades t
      JOIN instruments i ON i.instrument_id = t.instrument_id
      JOIN instruments u ON u.instrument_id = t.underlying_id
      LEFT JOIN instrument_specs s
             ON s.instrument_id = t.instrument_id
            AND (t.opened_at AT TIME ZONE 'Asia/Kolkata')::date
                BETWEEN s.valid_from AND s.valid_to
     WHERE t.segment = 'nse_stock'
       AND t.closed_at IS NOT NULL
       AND t.avg_entry > 0
       AND NOT (t.trade_id = ANY(:excluded))
     ORDER BY t.trade_id
    """
)

# One instrument, one bounded ts window — a PK (instrument_id, ts) range
# scan. Deliberately NOT a single join across every trade: an unbounded
# scan of this hypertable is what crippled the engine on 2026-09-16.
_PATH_SQL = text(
    """
    SELECT DISTINCT ON (date_trunc('minute', q.ts))
           date_trunc('minute', q.ts) AS m, q.ltp
      FROM option_quotes q
     WHERE q.instrument_id = :iid
       AND q.ts >= :frm
       AND q.ts <= :to
       AND q.ltp IS NOT NULL
     ORDER BY date_trunc('minute', q.ts), q.ts DESC
    """
)


async def load_trades() -> list[TradeInput]:
    sm = get_sessionmaker()
    out: list[TradeInput] = []
    async with sm() as session:
        rows = (await session.execute(_TRADES_SQL, {"excluded": list(NON_ATTRIBUTABLE)})).all()
        for r in rows:
            _, close_utc = session_window_utc(Segment.NSE_STOCK.market, r.sdate)
            path = (
                await session.execute(
                    _PATH_SQL,
                    {"iid": r.instrument_id, "frm": r.opened_at, "to": close_utc},
                )
            ).all()
            if len(path) < 2:
                continue
            out.append(
                TradeInput(
                    trade_id=r.trade_id,
                    underlying_symbol=r.usym,
                    opened_at=r.opened_at,
                    session_date=r.sdate,
                    avg_entry=Decimal(str(r.avg_entry)),
                    lot_size=int(r.lot_size),
                    expiry=r.expiry,
                    path=tuple((m, Decimal(str(ltp))) for m, ltp in path),
                )
            )
    return out


def _stats(outs: list[Outcome]) -> dict[str, float]:
    nets = [float(o.net) for o in outs]
    wins = [n for n in nets if n > 0]
    losses = [-n for n in nets if n < 0]
    return {
        "n": len(outs),
        "net": sum(nets),
        "mean_r": statistics.mean(o.r for o in outs) if outs else 0.0,
        "win_pct": 100 * len(wins) / len(outs) if outs else 0.0,
        "pf": (sum(wins) / sum(losses)) if losses else float("inf"),
    }


def _fmt(s: dict[str, float]) -> str:
    pf = "  inf" if s["pf"] == float("inf") else f"{s['pf']:5.2f}"
    return f"{s['n']:>4.0f} {s['net']:>10,.0f} {s['mean_r']:>7.3f} {s['win_pct']:>6.1f}% {pf}"


async def main() -> None:
    trades = await load_trades()
    sessions = sorted({t.session_date for t in trades})
    mid = sessions[len(sessions) // 2]
    print(
        f"\n{len(trades)} attributable nse_stock trades with a usable quote path, "
        f"{len(sessions)} sessions ({sessions[0]} .. {sessions[-1]})"
    )
    print(
        "All replayed at 1 lot of the REAL lot size. Rupee totals are comparable "
        "BETWEEN variants only, never against the recorded book.\n"
    )

    variants = [
        Variant(scratch_minutes=s, runner_guard=g)
        for s in (15, 25, 40, None)
        for g in (False, True)
    ]

    hdr = f"{'variant':<26} {'n':>4} {'net Rs':>10} {'mean R':>7} {'win':>7} {'PF':>5}"
    print(hdr)
    print("-" * len(hdr))
    results: dict[str, list[Outcome]] = {}
    for v in variants:
        outs = [replay(t, v) for t in trades]
        results[v.label] = outs
        print(f"{v.label:<26} {_fmt(_stats(outs))}")

    print("\nOut-of-sample split (by session, first half | second half)")
    print(f"{'variant':<26} {'1st net':>10} {'1st PF':>7} {'2nd net':>10} {'2nd PF':>7}")
    print("-" * 64)
    for label, outs in results.items():
        a = _stats([o for o in outs if o.session_date < mid])
        b = _stats([o for o in outs if o.session_date >= mid])
        pa = "inf" if a["pf"] == float("inf") else f"{a['pf']:.2f}"
        pb = "inf" if b["pf"] == float("inf") else f"{b['pf']:.2f}"
        print(f"{label:<26} {a['net']:>10,.0f} {pa:>7} {b['net']:>10,.0f} {pb:>7}")

    print("\nLeave-one-out: total with the single best trade removed")
    print(f"{'variant':<26} {'net Rs':>10} {'drop':>10}")
    print("-" * 48)
    for label, outs in results.items():
        tot = sum(float(o.net) for o in outs)
        best = max(float(o.net) for o in outs)
        print(f"{label:<26} {tot - best:>10,.0f} {best:>10,.0f}")

    print("\nExit-reason mix (first exit leg per trade)")
    for label, outs in results.items():
        c = Counter(o.reasons[0] if o.reasons else "NONE" for o in outs)
        top = "  ".join(f"{k}={v}" for k, v in c.most_common(6))
        print(f"{label:<26} {top}")


if __name__ == "__main__":
    asyncio.run(main())
