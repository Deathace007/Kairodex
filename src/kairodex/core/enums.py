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
