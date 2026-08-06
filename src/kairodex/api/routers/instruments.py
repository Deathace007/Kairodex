"""ARCHITECTURE.md §15 — `GET /api/instruments/{id}/chain?at=<ts>`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.api.deps import get_session, parse_iso_date
from kairodex.core.enums import InstrumentKind
from kairodex.store.models import Instrument, OptionQuote

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("/{instrument_id}/chain")
async def instrument_chain(
    instrument_id: int,
    at: str | None = Query(None, description="ISO timestamp; defaults to latest"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    underlying = await session.get(Instrument, instrument_id)
    if underlying is None:
        raise HTTPException(404, f"no instrument {instrument_id}")

    # Instrument.underlying_id is never populated by any ingest path (P2
    # §8c's documented gap, still open) — legs are matched by
    # underlying_symbol, the same workaround every other reader in this
    # codebase already uses (features.loader.load_chain,
    # data.recorder._resolve_ws_keys).
    legs = list(
        await session.scalars(
            select(Instrument).where(
                Instrument.kind == InstrumentKind.OPTION,
                Instrument.underlying_symbol == underlying.symbol,
            )
        )
    )
    at_dt = parse_iso_date(at, "at")

    out = []
    for leg in legs:
        stmt = select(OptionQuote).where(OptionQuote.instrument_id == leg.instrument_id)
        stmt = stmt.where(OptionQuote.ts <= at_dt) if at_dt else stmt
        quote = await session.scalar(stmt.order_by(OptionQuote.ts.desc()).limit(1))
        if quote is None:
            continue
        out.append(
            {
                "instrument_id": leg.instrument_id,
                "strike": leg.strike,
                "option_type": leg.option_type,
                "expiry": leg.expiry,
                "ts": quote.ts,
                "bid": quote.bid,
                "ask": quote.ask,
                "ltp": quote.ltp,
                "oi": quote.oi,
                "iv": quote.iv,
                "delta": quote.delta,
            }
        )
    out.sort(key=lambda leg: (leg["expiry"], leg["strike"], leg["option_type"]))
    return {"underlying_symbol": underlying.symbol, "as_of": at_dt, "legs": out}
