"""Cost models (ARCHITECTURE.md §12): "Rates live in config, seeded from
current published values — verify against a real contract note before
trusting backtest P&L." Repeating that verbatim because it's the honest
state of every rate below: current-best-knowledge published figures, not
independently verified against an actual broker contract note. Treat any
backtest P&L run through these as directionally right, not exact, until
that verification happens.
"""

from __future__ import annotations

from decimal import Decimal

from kairodex.core.enums import Side
from kairodex.execution.types import CostBreakdown

# --- NSE (India) — rates as fractions of premium unless noted -------------
_NSE_BROKERAGE_FLAT = Decimal("20")  # per executed order
_NSE_BROKERAGE_PCT = Decimal("0.0003")  # 0.03% — brokerage is min(flat, pct*premium)
_NSE_STT_SELL_PCT = Decimal("0.001")  # 0.1% of premium, sell side only
_NSE_EXCHANGE_TXN_PCT = Decimal("0.00035")  # 0.035% of premium, both sides
_NSE_SEBI_FEE_PCT = Decimal("0.000001")  # Rs 10 per crore = 0.0001%, both sides
_NSE_STAMP_DUTY_BUY_PCT = Decimal("0.00003")  # 0.003% of premium, buy side only
_NSE_GST_RATE = Decimal("0.18")  # on brokerage + exchange charge + SEBI fee only —
# NOT on STT or stamp duty, which are themselves taxes, not services.


def compute_nse_costs(side: Side, premium: Decimal, qty: int = 0) -> CostBreakdown:
    """`qty` is accepted but unused, purely for interface parity with
    `compute_us_costs` — both need to be callable through the same
    `CostModelFn` shape (`kairodex.execution.simulator`) so either is
    pluggable into `SimulatedBroker`. NSE's real costs genuinely are all
    premium-based, not per-contract, so ignoring `qty` here isn't a
    shortcut, it's the correct economics."""
    brokerage = min(_NSE_BROKERAGE_FLAT, _NSE_BROKERAGE_PCT * premium)
    exchange_txn_charge = _NSE_EXCHANGE_TXN_PCT * premium
    sebi_fee = _NSE_SEBI_FEE_PCT * premium
    stt = _NSE_STT_SELL_PCT * premium if side is Side.SELL else Decimal(0)
    stamp_duty = _NSE_STAMP_DUTY_BUY_PCT * premium if side is Side.BUY else Decimal(0)

    regulatory_fees = stt + exchange_txn_charge + sebi_fee + stamp_duty
    gst = _NSE_GST_RATE * (brokerage + exchange_txn_charge + sebi_fee)
    return CostBreakdown(brokerage=brokerage, regulatory_fees=regulatory_fees, taxes=gst)


# --- US — per-contract rates unless noted ----------------------------------
_US_COMMISSION_PER_CONTRACT = Decimal("0.65")
_US_OCC_FEE_PER_CONTRACT = Decimal("0.03")
_US_ORF_PER_CONTRACT = Decimal("0.02")
_US_SEC_FEE_RATE = Decimal("0.000008")  # $8 per $1,000,000 notional, sell side only
_US_TAF_PER_CONTRACT = Decimal("0.00218")  # FINRA Trading Activity Fee, sell side only


def compute_us_costs(side: Side, premium: Decimal, qty: int) -> CostBreakdown:
    commission = _US_COMMISSION_PER_CONTRACT * qty
    occ_fee = _US_OCC_FEE_PER_CONTRACT * qty
    orf = _US_ORF_PER_CONTRACT * qty
    sec_fee = _US_SEC_FEE_RATE * premium if side is Side.SELL else Decimal(0)
    taf = _US_TAF_PER_CONTRACT * qty if side is Side.SELL else Decimal(0)

    regulatory_fees = occ_fee + orf + sec_fee + taf
    return CostBreakdown(brokerage=commission, regulatory_fees=regulatory_fees, taxes=Decimal(0))
