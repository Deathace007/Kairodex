"""The `@register` decorator (ARCHITECTURE.md §9) — each entry declares
what a feature needs and how good it is, so the promotion pipeline (P3)
can route strategies that depend on non-backtestable or proxy features
without every strategy author having to know that by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kairodex.features.types import FeatureContext, Fidelity, Quality, Tier

FeatureFn = Callable[[FeatureContext], float | None]

# Bump whenever a registered feature's *formula* changes in a way that would
# make an old feature_vectors row mean something different — ARCHITECTURE.md
# §5.3's `registry_version` column exists so a formula change never silently
# mixes with old values under the same name. Not per-feature: one version
# for the whole registry is the simplest thing that satisfies "never
# silently reinterpret history," and matches the single JSONB blob per row.
REGISTRY_VERSION = "1"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    fn: FeatureFn
    inputs: tuple[str, ...]
    tier: Tier
    fidelity: Fidelity
    backtestable: dict[str, bool]
    cost_ms: float


_REGISTRY: dict[str, FeatureSpec] = {}


def register(
    *,
    name: str,
    inputs: list[str],
    tier: Tier,
    fidelity: Fidelity,
    backtestable: dict[str, bool],
    cost_ms: float,
) -> Callable[[FeatureFn], FeatureFn]:
    def decorator(fn: FeatureFn) -> FeatureFn:
        if name in _REGISTRY:
            raise ValueError(f"feature {name!r} already registered")
        _REGISTRY[name] = FeatureSpec(
            name=name,
            fn=fn,
            inputs=tuple(inputs),
            tier=tier,
            fidelity=fidelity,
            backtestable=dict(backtestable),
            cost_ms=cost_ms,
        )
        return fn

    return decorator


def get(name: str) -> FeatureSpec:
    return _REGISTRY[name]


def all_specs() -> list[FeatureSpec]:
    return list(_REGISTRY.values())


def compute_all(ctx: FeatureContext) -> tuple[dict[str, float], dict[str, str]]:
    """Evaluate every registered feature against one context. A feature
    that raises or returns None is recorded as MISSING in `quality` rather
    than aborting the rest — ARCHITECTURE.md §7's "poisoned rows never
    silently dropped" applies here too: one bad input shouldn't blank out
    every other feature for the same evaluation tick."""
    values: dict[str, float] = {}
    quality: dict[str, str] = {}
    for spec in _REGISTRY.values():
        try:
            result = spec.fn(ctx)
        except Exception:
            quality[spec.name] = Quality.MISSING.value
            continue
        if result is None:
            quality[spec.name] = Quality.MISSING.value
            continue
        values[spec.name] = result
        quality[spec.name] = Quality.OK.value
    return values, quality
