"""Every test mutates exactly ONE field off a baseline that passes every
gate, so a failure pinpoints exactly which gate's logic broke — and
confirms both the denial reason AND that the chain actually stopped
there (didn't keep evaluating gates after the first denial)."""

import dataclasses
import datetime
from decimal import Decimal

import pytest

from kairodex.config.segments import get_segment_config
from kairodex.core.enums import Segment, Side
from kairodex.risk.gates import GATE_CHAIN, run_gate_chain
from kairodex.risk.types import AccountState, TradeProposal

_NOW = datetime.datetime(2026, 8, 5, 10, 0, tzinfo=datetime.UTC)
_CONFIG = get_segment_config(Segment.NSE_STOCK)  # capital=50000, base_risk=0.08, ceiling=0.35,
# max_premium=0.35, max_concurrent=1, daily_loss=0.16, weekly=0.30, drawdown=0.40, exposure=0.40


def _account(**overrides: object) -> AccountState:
    base = AccountState(
        segment=Segment.NSE_STOCK,
        equity=Decimal(50000),
        high_water_mark=Decimal(50000),
        daily_pnl=Decimal(0),
        weekly_pnl=Decimal(0),
        consecutive_losses=0,
        breaker_status="ARMED",
        breaker_reason=None,
        blocked_until=None,
        kill_switch_engaged=False,
        open_positions_count=0,
        open_underlyings=frozenset(),
        total_exposure=Decimal(0),
        last_loss_ts_by_underlying={},
        session_open=True,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def _proposal(**overrides: object) -> TradeProposal:
    base = TradeProposal(
        segment=Segment.NSE_STOCK,
        underlying_symbol="RELIANCE",
        direction=Side.BUY,
        confidence=0.7,
        ts=_NOW,
        premium_per_lot=Decimal(100),
        lot_size=25,  # total premium = 2500
        stop_distance=Decimal(20),
        liquidity_score=0.8,
        spread_pct=0.02,
        contract_oi=1000,
        contract_volume=500,
        chain_complete=True,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_baseline_passes_every_gate():
    result = run_gate_chain(_proposal(), _account(), _CONFIG, now=_NOW)
    assert result.allowed
    assert len(result.evaluated) == len(GATE_CHAIN)
    assert all(g.allowed for g in result.evaluated)
    assert result.reject_stage is None
    assert result.reject_reason is None


def test_kill_switch_denies_first():
    result = run_gate_chain(_proposal(), _account(kill_switch_engaged=True), _CONFIG, now=_NOW)
    assert not result.allowed
    assert result.reject_stage == "kill_switch"
    assert result.reject_reason == "KILL_SWITCH_ENGAGED"
    assert len(result.evaluated) == 1  # stopped immediately, didn't run the rest


def test_breaker_tripped():
    account = _account(breaker_status="TRIPPED", breaker_reason="daily loss")
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "breaker_state"
    assert result.reject_reason == "BREAKER_TRIPPED:daily loss"
    assert len(result.evaluated) == 2


def test_session_closed():
    result = run_gate_chain(_proposal(), _account(session_open=False), _CONFIG, now=_NOW)
    assert result.reject_stage == "session_window"
    assert len(result.evaluated) == 3


def test_daily_loss_limit_reached():
    """limit = 0.16 * 50000 = 8000; a loss of exactly 9000 exceeds it."""
    account = _account(daily_pnl=Decimal(-9000))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "daily_loss"
    assert len(result.evaluated) == 6  # kill, breaker, session, warmup, event_blackout, daily_loss


def test_daily_loss_at_exact_limit_denies():
    """Boundary: exactly at the limit (>=) denies, not just past it."""
    account = _account(daily_pnl=Decimal(-8000))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "daily_loss"


def test_daily_loss_just_under_limit_passes():
    account = _account(daily_pnl=Decimal(-7999))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.allowed


def test_weekly_loss_limit_reached():
    """limit = 0.30 * 50000 = 15000."""
    account = _account(weekly_pnl=Decimal(-16000))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "weekly_loss"
    assert len(result.evaluated) == 7


def test_drawdown_throttle():
    """50% drawdown from HWM >= max_drawdown_pct (0.40)."""
    account = _account(equity=Decimal(25000), high_water_mark=Decimal(50000))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "drawdown_throttle"
    assert len(result.evaluated) == 8


def test_max_concurrent_reached():
    account = _account(open_positions_count=5)  # == max_concurrent for NSE_STOCK (5)
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "max_concurrent"
    assert len(result.evaluated) == 9


def test_exposure_cap_exceeded():
    """cap = 0.40 * 50000 = 20000; existing exposure 19000 + this trade's
    2500 = 21500 -> 0.43, over the cap."""
    account = _account(total_exposure=Decimal(19000))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "exposure"
    assert len(result.evaluated) == 10


def test_correlation_cluster_same_underlying_already_open():
    account = _account(open_underlyings=frozenset({"RELIANCE"}))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "correlation_cluster"
    assert result.reject_reason == "ALREADY_OPEN_ON_UNDERLYING"
    assert len(result.evaluated) == 11


def test_correlation_cluster_reentry_cooldown():
    """cooldown = 30 minutes; a loss 10 minutes ago is still within it."""
    ten_min_ago = _NOW - datetime.timedelta(minutes=10)
    account = _account(last_loss_ts_by_underlying={"RELIANCE": ten_min_ago})
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "correlation_cluster"
    assert result.reject_reason == "REENTRY_COOLDOWN_ACTIVE"


def test_correlation_cluster_cooldown_expired_passes():
    thirty_one_min_ago = _NOW - datetime.timedelta(minutes=31)
    account = _account(last_loss_ts_by_underlying={"RELIANCE": thirty_one_min_ago})
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.allowed


def test_liquidity_unknown_fails_closed():
    result = run_gate_chain(_proposal(liquidity_score=None), _account(), _CONFIG, now=_NOW)
    assert result.reject_stage == "liquidity"
    assert result.reject_reason == "LIQUIDITY_UNKNOWN"
    assert len(result.evaluated) == 12


def test_liquidity_too_low():
    result = run_gate_chain(_proposal(liquidity_score=0.1), _account(), _CONFIG, now=_NOW)
    assert result.reject_stage == "liquidity"
    assert result.reject_reason == "LIQUIDITY_TOO_LOW"


def test_liquidity_incomplete_chain():
    result = run_gate_chain(_proposal(chain_complete=False), _account(), _CONFIG, now=_NOW)
    assert result.reject_stage == "liquidity"
    assert result.reject_reason == "INCOMPLETE_CHAIN"


def test_capital_available_exceeds_max_premium_pct():
    """max_premium_pct=0.35 -> affordability cap = 17500. exposure_cap_pct
    is looser (0.40 -> 20000), so a premium of 18000 clears exposure
    (18000/50000=0.36 <= 0.40, passes) but still breaches affordability
    (18000 > 17500) — a real, independently-reachable gap between the two
    caps, not masked by exposure_gate firing first."""
    proposal = _proposal(premium_per_lot=Decimal(720), lot_size=25)  # 18000
    result = run_gate_chain(proposal, _account(), _CONFIG, now=_NOW)
    assert result.reject_stage == "capital_available"
    assert result.reject_reason == "EXCEEDS_MAX_PREMIUM_PCT"
    assert len(result.evaluated) == 13  # every gate ran


def test_capital_available_unreachable_once_exposure_gate_has_passed():
    """The invariant capital_available_gate's docstring relies on: once
    exposure_gate has passed, this trade's premium is always <= equity -
    total_exposure, for any combination that clears the exposure cap.
    Swept across a range up to (but not past) the cap, since this is a
    mathematical guarantee, not a coincidence at one input."""
    for total_exposure in (0, 5000, 15000, 17500):  # all clear the 20000 exposure cap
        account = _account(total_exposure=Decimal(total_exposure))
        result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
        assert result.allowed, f"total_exposure={total_exposure}: {result.reject_reason}"

    # One past the cap: exposure_gate catches it, capital_available never runs.
    account = _account(total_exposure=Decimal(19999))
    result = run_gate_chain(_proposal(), account, _CONFIG, now=_NOW)
    assert result.reject_stage == "exposure"


def test_gate_order_matches_architecture_doc():
    """ARCHITECTURE.md §11's literal order."""
    names = [g.__name__ for g in GATE_CHAIN]
    assert names == [
        "kill_switch_gate",
        "breaker_gate",
        "session_window_gate",
        # Not in the doc's numbered order — a finer edge on the same
        # "is this a tradable moment" question session_window asks, so it
        # sits immediately after it rather than inventing a stage
        # elsewhere. See session_warmup_gate's docstring.
        "session_warmup_gate",
        "event_blackout_gate",
        "daily_loss_gate",
        "weekly_loss_gate",
        "drawdown_throttle_gate",
        "max_concurrent_gate",
        "exposure_gate",
        "correlation_cluster_gate",
        "liquidity_gate",
        "capital_available_gate",
    ]


@pytest.mark.parametrize("segment", list(Segment))
def test_baseline_passes_for_every_segment_config(segment):
    config = get_segment_config(segment)
    capital = Decimal(str(config.capital))
    account = _account(segment=segment, equity=capital, high_water_mark=capital)
    proposal = _proposal(segment=segment, premium_per_lot=Decimal("1"), lot_size=1)
    result = run_gate_chain(proposal, account, config, now=_NOW)
    assert result.allowed


def test_session_warmup_rejects_at_the_opening_bell():
    """09:18 IST = 03:48 UTC, 3 minutes in, against nse_stock's 20-minute
    warm-up. That is the minute five of the thirteen NSE entries ever
    taken were opened at."""
    at_open = datetime.datetime(2026, 8, 5, 3, 48, tzinfo=datetime.UTC)
    result = run_gate_chain(_proposal(), _account(), _CONFIG, now=at_open)
    assert result.reject_stage == "session_warmup"
    assert result.reject_reason == "SESSION_WARMUP"
    assert len(result.evaluated) == 4  # kill, breaker, session, warmup


def test_session_warmup_allows_once_the_window_has_passed():
    """09:36 IST = 04:06 UTC, 21 minutes in — past the 20-minute floor."""
    warmed = datetime.datetime(2026, 8, 5, 4, 6, tzinfo=datetime.UTC)
    assert run_gate_chain(_proposal(), _account(), _CONFIG, now=warmed).allowed


def test_session_warmup_defers_to_session_window_when_market_is_shut():
    """Out of hours this gate has no opinion — `session_window_gate` owns
    that rejection, and two gates reporting the same fact under different
    names would make the rejection breakdown lie about why."""
    saturday = datetime.datetime(2026, 8, 8, 5, 0, tzinfo=datetime.UTC)
    result = run_gate_chain(_proposal(), _account(session_open=False), _CONFIG, now=saturday)
    assert result.reject_stage == "session_window"
