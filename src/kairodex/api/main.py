"""FastAPI app assembly (ARCHITECTURE.md §15). `kairodex api` (cli.py)
runs this under uvicorn, bound to 127.0.0.1 by default — SPEC_REVIEW.md
B5: "Single user. API binds to localhost..."; this process holds
`POST /api/kill` and strategy promotion, so it is not meant to be
reachable from the open internet. Reach it from a laptop against the VM
deployment via an SSH tunnel (`ssh -L 8000:localhost:8000 ...`), the same
access pattern already used for everything else in this repo — not a new
firewall rule.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kairodex.api import ws
from kairodex.api.routers import (
    backtests,
    control,
    exports,
    health,
    instruments,
    master,
    research,
    segments,
    strategies,
)

app = FastAPI(title="Kairodex API", version="1")

# The Next.js dev server runs on a different port than uvicorn even when
# both are on localhost — CORS has to allow that or every fetch from
# `npm run dev` fails with no server-side error to explain why. Loose on
# purpose (this API is localhost-only per the module docstring above, not
# reachable from anywhere CORS would actually be protecting against).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(segments.router, prefix="/api")
app.include_router(master.router, prefix="/api")
app.include_router(instruments.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")
app.include_router(research.router, prefix="/api")
app.include_router(control.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(backtests.router, prefix="/api")
app.include_router(ws.router)
