"""The dominant reason us_index never traded (docs/PROGRESS.md §13):
run_entry_tick used to commit to the single nearest loaded expiry snapshot
and give up if THAT one's DTE fell outside select_contract's own
min_dte/max_dte window — even with a second, perfectly good snapshot
sitting unused in the same feature_ctx.chain.

SPY/QQQ/IWM trade near-daily expiries, so the nearest one is almost always
0 DTE (today), which min_dte=1 correctly rejects. Traced live 2026-08-06:
all three had a second, 1-DTE snapshot that would select cleanly, never
tried."""

import datetime
from decimal import Decimal

from kairodex.core.enums import Side
from kairodex.data.types import ChainSnapshot, Tick
from kairodex.engine.orchestrator import _select_across_expiries

_TODAY = datetime.date(2026, 8, 6)
_TS = datetime.datetime(2026, 8, 6, 14, 0, tzinfo=datetime.UTC)


def _tick(strike: int, **over: object) -> Tick:
    base: dict[str, object] = {
        "instrument_key": f"C{strike}",
        "ts": _TS,
        "strike": Decimal(strike),
        "option_type": "C",
        # 1.6% of mid — inside select_contract's relative-spread limit, so
        # these expiry-fallthrough tests are not silently testing the spread
        # filter. Was 4.90/5.10 (4%), which the filter added 2026-08-14 rejects.
        "bid": Decimal("4.96"),
        "ask": Decimal("5.04"),
        "bid_sz": 50,
        "ask_sz": 50,
    }
    base.update(over)
    return Tick(**base)  # type: ignore[arg-type]


def _snapshot(expiry: datetime.date, strikes: list[int]) -> ChainSnapshot:
    return ChainSnapshot(
        underlying="SPY", expiry=expiry, ts=_TS, quotes=[_tick(k) for k in strikes]
    )


def _select(chain: list[ChainSnapshot]):
    return _select_across_expiries(
        chain,
        Side.BUY,
        spot=Decimal(100),
        equity=Decimal(50000),
        max_premium_pct=0.35,
        lot_size=100,
        as_of=_TODAY,
        synthetic_quotes=False,
    )


def test_falls_through_to_the_next_expiry_when_the_nearest_is_below_min_dte():
    """0 DTE (today) legs exist but every one is outside min_dte=1; the
    1 DTE snapshot has a perfectly good ATM strike. The old single-snapshot
    code never looked at it."""
    chain = [
        _snapshot(_TODAY, [95, 100, 105]),  # 0 DTE — always rejected
        _snapshot(_TODAY + datetime.timedelta(days=1), [95, 100, 105]),  # 1 DTE
    ]
    result = _select(chain)
    assert result.selected is not None
    assert result.selected.expiry == _TODAY + datetime.timedelta(days=1)


def test_prefers_the_nearest_expiry_that_actually_works():
    """Not just 'any expiry' — the nearest one that clears selection,
    checked by making both the 1 DTE and 5 DTE snapshots tradeable and
    confirming the nearer one wins."""
    chain = [
        _snapshot(_TODAY, [100]),  # 0 DTE, rejected
        _snapshot(_TODAY + datetime.timedelta(days=1), [100]),  # should win
        _snapshot(_TODAY + datetime.timedelta(days=5), [100]),
    ]
    result = _select(chain)
    assert result.selected is not None
    assert result.selected.expiry == _TODAY + datetime.timedelta(days=1)


def test_reports_no_candidates_when_that_is_genuinely_the_only_reason():
    """When no expiry has any usable leg at all, the reason is honestly
    NO_CANDIDATES_IN_EXPIRY_WINDOW — the caller's `selected is None` check
    is unaffected."""
    chain = [
        _snapshot(_TODAY, []),  # no legs at all
        _snapshot(_TODAY + datetime.timedelta(days=1), []),
    ]
    result = _select(chain)
    assert result.selected is None
    assert result.reason == "NO_CANDIDATES_IN_EXPIRY_WINDOW"


def test_reports_the_substantive_reason_not_the_0dte_expiry_window_miss():
    """Verified live 2026-08-06 on IWM: the 0-DTE snapshot returns
    NO_CANDIDATES_IN_EXPIRY_WINDOW (expected — it's below min_dte and says
    nothing about why no trade happened), while the 1-DTE snapshot that
    actually qualified failed for a real reason. Reporting the nearest
    snapshot's reason recorded the wrong cause in `signals`, which
    ARCHITECTURE.md §11 treats as training data — and it sent a whole
    investigation chasing an expiry-window problem that did not exist.

    Here the 0 DTE legs are out-of-window; the 1 DTE leg is a 0.005-delta
    lottery ticket that the delta cap rejects. The substantive reason must
    win."""
    # Tight spread (1.33% of mid) on purpose: this test is about the
    # substantive reason winning over the 0-DTE expiry-window miss, so the
    # leg must fail on DELTA and nothing else. At the original 1/2 quote it
    # was a 67%-of-mid spread and reported NO_CONTRACT_INSIDE_SPREAD_LIMIT.
    lottery = _tick(400, delta=Decimal("0.005"), bid=Decimal("1.49"), ask=Decimal("1.51"))
    chain = [
        _snapshot(_TODAY, [100]),  # 0 DTE -> NO_CANDIDATES_IN_EXPIRY_WINDOW
        ChainSnapshot(
            underlying="IWM",
            expiry=_TODAY + datetime.timedelta(days=1),
            ts=_TS,
            quotes=[lottery],
        ),
    ]
    result = _select(chain)
    assert result.selected is None
    assert result.reason == "NO_CONTRACT_NEAR_TARGET_DELTA"


def test_single_expiry_chain_behaves_exactly_as_before():
    """The common case (only one expiry loaded, or the nearest already
    works) must be unchanged — this is a fallback, not a rewrite of
    ordinary selection."""
    chain = [_snapshot(_TODAY + datetime.timedelta(days=1), [95, 100, 105])]
    result = _select(chain)
    assert result.selected is not None
    assert result.selected.expiry == _TODAY + datetime.timedelta(days=1)
