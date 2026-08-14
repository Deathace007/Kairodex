"""The feature backfill is only useful if it reproduces what the engine
actually saw. Two of its inputs are supplied by the caller rather than by
`build_context`, and both have already been the cause of a
silently-dead detector in this codebase:

- `prior_as_of=None` leaves `prior_chain=[]`, which makes `oi_change`
  return None, which makes `oi_price_flow_detector` return None. That was
  live across 20,399 signals before §18a caught it.
- `index_bars` is deliberately left to the caller by `build_context`'s own
  docstring, and nothing supplying it kept `relative_strength` dead over
  the same period.

If the backfill and the live engine disagree on either, the backfilled
rows are a *different feature set* wearing the same column names, and a
model trained across the join learns the seam rather than the market.
These tests pin the agreement at the source level, since exercising it
for real needs a live DB (VM only, per CLAUDE.md).
"""

import ast
import inspect
from pathlib import Path

from kairodex.backtest import backfill_features
from kairodex.engine import live_loop


def _keyword_source(module, func_name: str, call_name: str, kwarg: str) -> str:
    """Return the source text of `kwarg` passed to `call_name` inside
    `func_name`. Compares intent at the call site rather than re-running
    the engine, which would need a database."""
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name != call_name:
            continue
        for kw in node.keywords:
            if kw.arg == kwarg:
                return ast.unparse(kw.value)
    raise AssertionError(f"no {call_name}(..., {kwarg}=...) found in {module.__name__}")


def test_backfill_uses_the_same_oi_lookback_as_the_live_engine():
    """Both must offset by `flow.OI_LOOKBACK`. A literal on either side
    would drift the moment the detector's window is retuned."""
    live = _keyword_source(live_loop, "run", "run_entry_tick", "prior_as_of")
    back = _keyword_source(
        backfill_features, "backfill_feature_vectors", "build_context", "prior_as_of"
    )
    assert "OI_LOOKBACK" in live
    assert "OI_LOOKBACK" in back


def test_backfill_injects_index_bars_like_the_engine_does():
    """`relative_strength_vs_index` and `index_correlation` are both None
    without this, so the backfill would produce 16 usable features where
    the engine produces 18."""
    src = inspect.getsource(backfill_features.backfill_feature_vectors)
    assert "load_index_bars" in src
    assert "index_bars=index_bars" in src


def test_backfill_goes_through_the_shared_persist_path():
    """`compute_and_store`, not a private copy of compute+insert — the
    same single code path the engine uses, so the two cannot diverge in
    what they store or how they key it."""
    src = inspect.getsource(backfill_features.backfill_feature_vectors)
    assert "feature_store.compute_and_store" in src


def test_backfill_links_the_signal_to_the_vector():
    """A stored feature row nobody can join to a label is not a training
    set. `signals.feature_vector_id` was NULL on all 79,209 rows before
    this existed."""
    src = inspect.getsource(backfill_features.backfill_feature_vectors)
    assert "signal.feature_vector_id = feature_vector_id" in src
