"""The app assembles without a DB connection — every router import
resolves, no duplicate route registration. Doesn't hit any endpoint
(every route here touches the DB; live-verified on the VM instead, this
repo's established convention for DB-touching code)."""

from kairodex.api.main import app


def _all_paths(routes: list[object], prefix: str = "") -> list[str]:
    """FastAPI (this version) keeps `include_router`-added routes behind
    a lazy `_IncludedRouter` wrapper rather than flattening them onto
    `app.routes` directly, and the router's own `prefix=` (e.g. "/api")
    lives on the wrapper's `include_context`, not baked into each route's
    own `.path` — recurse through both to reach real full paths."""
    out: list[str] = []
    for r in routes:
        nested = getattr(r, "original_router", None)
        if nested is not None:
            nested_prefix = prefix + getattr(r.include_context, "prefix", "")  # type: ignore[attr-defined]
            out.extend(_all_paths(nested.routes, nested_prefix))
        else:
            out.append(prefix + r.path)  # type: ignore[attr-defined]
    return out


def test_app_has_every_named_endpoint():
    paths = set(_all_paths(app.routes))
    for expected in [
        "/api/health",
        "/api/health/feeds",
        "/api/segments",
        "/api/segments/{segment}/overview",
        "/api/segments/{segment}/positions",
        "/api/segments/{segment}/opportunities",
        "/api/segments/{segment}/trades",
        "/api/segments/{segment}/trades/{trade_id}",
        "/api/segments/{segment}/signals",
        "/api/segments/{segment}/performance",
        "/api/segments/{segment}/equity-curve",
        "/api/segments/{segment}/analytics/breakdown",
        "/api/segments/{segment}/risk",
        "/api/master/overview",
        "/api/instruments/{instrument_id}/chain",
        "/api/strategies",
        "/api/strategies/{strategy_id}/report",
        "/api/strategies/{strategy_id}/promote",
        "/api/research/notes",
        "/api/segments/{segment}/breaker",
        "/api/kill",
        "/api/exports",
        "/api/exports/{export_id}",
        "/api/backtests",
        "/api/backtests/{run_id}",
        "/ws/stream",
    ]:
        assert expected in paths, f"missing route {expected}"


def test_no_duplicate_routes():
    assert len(_all_paths(app.routes)) == len(set(_all_paths(app.routes)))
