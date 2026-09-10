"""`load_chain`'s DISTINCT ON must stay bounded on both sides of `as_of`.

The upper bound is correctness (no lookahead — FeatureContext's docstring).
The lower bound is the reason the live engine works at all: without it the
query scans every historical option_quotes row per leg, which on the VM
took 13-28 minutes per call and reduced a trading session to one engine
cycle. See `_CHAIN_QUOTE_LOOKBACK`.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select

from kairodex.features.loader import _CHAIN_QUOTE_LOOKBACK
from kairodex.store.models import Instrument, OptionQuote


def _chain_predicates() -> str:
    """The same WHERE clause `load_chain` builds, compiled to SQL."""
    stmt = (
        select(OptionQuote)
        .join(Instrument, Instrument.instrument_id == OptionQuote.instrument_id)
        .where(
            OptionQuote.ts <= datetime.datetime(2026, 9, 10, tzinfo=datetime.UTC),
            OptionQuote.ts
            >= datetime.datetime(2026, 9, 10, tzinfo=datetime.UTC) - _CHAIN_QUOTE_LOOKBACK,
        )
    )
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_lookback_is_bounded_and_generous_enough_for_the_entry_path() -> None:
    # live_loop.ENTRY_MAX_QUOTE_AGE_MS is 90s: a leg this bound drops was
    # already too stale to fill against, so the window must sit above it.
    from kairodex.engine.live_loop import ENTRY_MAX_QUOTE_AGE_MS

    assert datetime.timedelta(milliseconds=ENTRY_MAX_QUOTE_AGE_MS) < _CHAIN_QUOTE_LOOKBACK
    # ...and small enough that the scan stays inside one daily chunk.
    assert datetime.timedelta(hours=1) >= _CHAIN_QUOTE_LOOKBACK


def test_load_chain_query_has_both_ts_bounds() -> None:
    import inspect

    from kairodex.features import loader

    src = inspect.getsource(loader.load_chain)
    assert "OptionQuote.ts <= as_of" in src
    assert "OptionQuote.ts >= as_of - _CHAIN_QUOTE_LOOKBACK" in src
    assert ">=" in _chain_predicates() and "<=" in _chain_predicates()


def test_live_loop_reads_the_clock_per_underlying() -> None:
    """A cycle-start `now` fed to the risk gates rejects a whole slow sweep
    as SESSION_WARMUP — the second half of the 2026-09-10 outage."""
    import inspect

    from kairodex.engine import live_loop

    src = inspect.getsource(live_loop.run_segment)
    body = src.split("for position, underlying in enumerate(underlyings", 1)[1]
    assert "now = clock.now()" in body.split("run_entry_tick", 1)[0]
