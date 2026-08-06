"""Enums shared across every layer. Kept minimal — add a member only when a
real requirement needs it (see docs/SPEC_REVIEW.md B1 for the US Index scope)."""

from __future__ import annotations

import enum


class Segment(enum.StrEnum):
    """The four independent trading segments (SPEC.md "Trading Segments").

    No code outside kairodex.config may branch on Segment to look up capital or
    risk numbers — those live in config/segments/*.yaml, not in Python.
    """

    NSE_STOCK = "nse_stock"
    NSE_INDEX = "nse_index"
    US_STOCK = "us_stock"
    US_INDEX = "us_index"

    @property
    def market(self) -> Market:
        return Market.NSE if self.value.startswith("nse_") else Market.US

    @property
    def currency(self) -> str:
        return "INR" if self.market is Market.NSE else "USD"


class Market(enum.StrEnum):
    NSE = "nse"
    US = "us"


class InstrumentKind(enum.StrEnum):
    UNDERLYING = "underlying"
    INDEX = "index"
    OPTION = "option"
    FUTURE = "future"


class ExerciseStyle(enum.StrEnum):
    EUROPEAN = "european"
    AMERICAN = "american"


class Settlement(enum.StrEnum):
    CASH = "cash"
    PHYSICAL = "physical"


class OptionType(enum.StrEnum):
    """Stored uniformly as C/P for both markets — vendor spellings (Upstox's
    CE/PE, LSE's C/P) are translated in kairodex.data.normalize, not stored as-is."""

    CALL = "C"
    PUT = "P"


class Side(enum.StrEnum):
    """Shared by `signals.direction` (bullish/bearish bias on the
    underlying, before a strategy/contract selector resolves that into an
    actual call or put — ARCHITECTURE.md §10) and `orders.side` (buy/sell
    the option contract itself, §12). This is an options-*buying* platform
    (ADR/SPEC scope) — BUY always means opening a long option position,
    SELL always means closing one; there is no short-option/naked-write
    path anywhere in this codebase."""

    BUY = "buy"
    SELL = "sell"


class StrategyStatus(enum.StrEnum):
    """The promotion state machine (ARCHITECTURE.md §10):
    DRAFT -> BACKTESTED -> VALIDATED -> SHADOW -> PAPER_SMALL -> PAPER_FULL,
    with RETIRED reachable from SHADOW/PAPER_SMALL/PAPER_FULL."""

    DRAFT = "draft"
    BACKTESTED = "backtested"
    VALIDATED = "validated"
    SHADOW = "shadow"
    PAPER_SMALL = "paper_small"
    PAPER_FULL = "paper_full"
    RETIRED = "retired"


# ARCHITECTURE.md §10's transition graph, literally. Lives here (not in
# kairodex.backtest.promotion, where it originated) so kairodex.api's
# `POST /strategies/{id}/promote` can validate a human-requested
# transition without importing kairodex.backtest at all — the
# import-linter contract "API is glue, not business logic" forbids that
# import outright, and this table is pure enum topology, not business
# logic (the actual gate *evaluation* — is a strategy good enough to
# promote — stays in kairodex.backtest.promotion, correctly off-limits
# to the API; this is only "which transitions are topologically legal").
_VALID_STRATEGY_TRANSITIONS: dict[StrategyStatus, frozenset[StrategyStatus]] = {
    StrategyStatus.DRAFT: frozenset({StrategyStatus.BACKTESTED}),
    StrategyStatus.BACKTESTED: frozenset({StrategyStatus.VALIDATED}),
    StrategyStatus.VALIDATED: frozenset({StrategyStatus.SHADOW}),
    StrategyStatus.SHADOW: frozenset({StrategyStatus.PAPER_SMALL, StrategyStatus.RETIRED}),
    StrategyStatus.PAPER_SMALL: frozenset({StrategyStatus.PAPER_FULL, StrategyStatus.RETIRED}),
    StrategyStatus.PAPER_FULL: frozenset({StrategyStatus.RETIRED}),
    StrategyStatus.RETIRED: frozenset(),
}


def is_valid_strategy_transition(from_status: StrategyStatus, to_status: StrategyStatus) -> bool:
    return to_status in _VALID_STRATEGY_TRANSITIONS.get(from_status, frozenset())
