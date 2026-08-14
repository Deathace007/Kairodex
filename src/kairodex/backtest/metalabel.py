"""Meta-labelling (López de Prado, *Advances in Financial Machine
Learning*, ch. 3): keep the existing strategy deciding the SIDE, and learn
a second model that only decides whether to ACT on that side, and how big.

Why this shape, specifically. PROGRESS.md §20c separated two things §19
had run together:

- **Direction is weakly right.** Mirroring every signal resolves 8.1%
  clean target hits against 31.6% as-signalled, so the detectors carry
  positive directional information. Flipping the side is decisively worse.
- **Confidence is backwards.** `signals.confidence` is monotonically
  anti-predictive in its own magnitude, at every band, out of sample.

That is exactly the setup meta-labelling addresses. The secondary model
never has to predict direction — the hard, competitive part — only
`P(this call succeeds | features)`, a binary problem on labels that
already exist. Its output is what `confidence` was supposed to be and
measurably is not, and it is also what position size should key off:
§20a found `corr(notional, return) = -1.2%`, i.e. size currently carries
no information at all.

**This module MEASURES. It does not trade and nothing imports it from the
live path.** Three of four hypotheses tested on 2026-08-14 died on the
per-session split (§20f), including two that looked excellent in
aggregate. The deliverable here is an honest number, and the number is
allowed to say "no edge" — which is worth knowing for the price of a
retrain, and far cheaper than another hand-written detector.

Methodology, and the traps it is avoiding:

- **Purging and embargo, not k-fold.** `backfill.py`'s own docstring is
  explicit that consecutive signals on one underlying share almost all of
  their forward window and "must not be treated as a sample size of n".
  Plain cross-validation would leak the answer across the split. This
  reuses `validation.walk_forward_splits` verbatim rather than
  reimplementing it, so the purge rule cannot drift from the one the
  promotion gates already use.
- **Fit statistics come from the training fold only.** Standardisation
  means and medians are computed in-fold and applied to the test fold.
  Standardising over the whole dataset first is a classic silent leak.
- **AUC, not accuracy.** At a ~32% base rate a model that always predicts
  "no" scores 68% accuracy and is useless. AUC asks the only question
  that matters here: does the score RANK winners above losers.
- **Effective n is far below nominal n.** Purging removes leakage across
  the train/test boundary; it does not make overlapping signals inside a
  fold independent. Treat the AUC as directional, not as something with a
  tight confidence interval.

No new dependency: logistic regression and AUC are ~30 lines of numpy,
and the first question is "is there any linear signal in these features
at all", which a linear model answers honestly. If this clears, a
gradient-booster is the natural follow-up and *then* the dependency earns
its place.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from decimal import Decimal

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kairodex.backtest.types import BacktestSignal, ForwardOutcome
from kairodex.backtest.validation import walk_forward_splits
from kairodex.core.enums import Segment, Side
from kairodex.store.models import FeatureVector, Signal

logger = logging.getLogger(__name__)

# The engine's own resolver horizon is 90 one-minute bars, so a signal's
# forward window can still be open 90 minutes after it fired. The embargo
# must be at least that or a trade straddling the fold boundary leaks.
# Doubled for margin, exactly the "embargo >= max holding period" rule
# `walk_forward_splits` documents.
DEFAULT_EMBARGO = datetime.timedelta(minutes=180)
_BAR_DAYS = 1.0 / 1440.0  # one-minute bars, in days, for _resolution_end

# Features that never populate, verified over the full backfill: `iv_rank`
# and `iv_percentile` need an IV history nothing supplies (and
# `option_quotes.iv` is NULL on all 64.8M rows), and
# `opening_range_position` needs `session_open_ts`, which `build_context`
# leaves "for the caller to set" and no caller sets. Excluded by name
# rather than by dropping all-NaN columns, so that if one of them starts
# populating it shows up as a deliberate decision rather than silently
# joining the model.
KNOWN_DEAD_FEATURES = frozenset({"iv_rank", "iv_percentile", "opening_range_position"})


@dataclasses.dataclass(frozen=True, slots=True)
class Dataset:
    signals: list[BacktestSignal]
    x: np.ndarray  # (n, d)
    y: np.ndarray  # (n,) 1 = the signal's own direction hit its target first
    feature_names: list[str]

    def __len__(self) -> int:
        return len(self.signals)


@dataclasses.dataclass(frozen=True, slots=True)
class FoldResult:
    test_start: datetime.datetime
    n_train: int
    n_test: int
    base_rate: float
    auc: float
    lift_top_decile: float


@dataclasses.dataclass(frozen=True, slots=True)
class MetalabelReport:
    n: int
    feature_names: list[str]
    base_rate: float
    folds: list[FoldResult]
    coefficients: dict[str, float]  # from a full-sample refit, for inspection only

    @property
    def mean_auc(self) -> float:
        return float(np.mean([f.auc for f in self.folds])) if self.folds else float("nan")

    @property
    def folds_beating_chance(self) -> int:
        return sum(1 for f in self.folds if f.auc > 0.5)


async def load_dataset(
    session: AsyncSession,
    *,
    segment: Segment,
    min_ts: datetime.datetime | None = None,
) -> Dataset:
    """Join `feature_vectors` (X) to `signals.forward_outcome` (y).

    Only rows carrying BOTH are usable, which is precisely what steps A
    (persist features live) and C (backfill them for history) produced —
    before those, `signals.feature_vector_id` was NULL on all 79,209 rows
    and this join returned nothing.
    """
    query = (
        select(Signal, FeatureVector)
        .join(FeatureVector, FeatureVector.id == Signal.feature_vector_id)
        .where(
            Signal.segment == segment,
            Signal.forward_outcome.isnot(None),
            Signal.feature_vector_id.isnot(None),
        )
        .order_by(Signal.ts)
    )
    if min_ts is not None:
        query = query.where(Signal.ts >= min_ts)
    rows = list((await session.execute(query)).all())
    if not rows:
        return Dataset([], np.empty((0, 0)), np.empty(0), [])

    # A stable, sorted feature order — the column order must not depend on
    # dict iteration of whichever row happened to come first.
    names = sorted(
        {k for _sig, fv in rows for k in fv.values if k not in KNOWN_DEAD_FEATURES}
    )

    signals: list[BacktestSignal] = []
    x_rows: list[list[float]] = []
    y_vals: list[int] = []
    for sig, fv in rows:
        fo = sig.forward_outcome
        outcome = ForwardOutcome(
            exit_reason=str(fo["exit_reason"]),
            exit_price=Decimal(str(fo["exit_price"])),
            bars_held=int(fo["bars_held"]),
            mfe=Decimal(str(fo["mfe"])),
            mae=Decimal(str(fo["mae"])),
            mfe_atr=float(fo["mfe_atr"]),
            mae_atr=float(fo["mae_atr"]),
            return_atr=float(fo["return_atr"]),
        )
        signals.append(
            BacktestSignal(
                ts=sig.ts,
                segment=segment,
                underlying_symbol=str(sig.underlying_id),
                direction=Side(sig.direction),
                confidence=float(sig.confidence),
                entry_price=Decimal(str(fo["entry_price"])),
                atr_at_entry=Decimal(str(fo["atr_at_entry"])),
                outcome=outcome,
                lead_time_pct=None,
            )
        )
        x_rows.append([_as_float(fv.values.get(n)) for n in names])
        # The meta-label: did the primary model's OWN call resolve to its
        # target before its stop? Not "did price go up" — the side is the
        # primary model's to choose and this only scores whether acting on
        # it worked.
        y_vals.append(1 if outcome.exit_reason == "TARGET" else 0)

    return Dataset(signals, np.asarray(x_rows, dtype=float), np.asarray(y_vals), names)


def _as_float(v: object) -> float:
    if v is None:
        return float("nan")
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return f if np.isfinite(f) else float("nan")


def _standardise(
    train: np.ndarray, test: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Impute with the TRAIN median and scale by TRAIN mean/std. Every
    statistic comes from the training fold — computing them over the whole
    dataset first is the classic silent leak."""
    med = np.nanmedian(train, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    tr = np.where(np.isfinite(train), train, med)
    te = np.where(np.isfinite(test), test, med)
    mu = tr.mean(axis=0)
    sd = tr.std(axis=0)
    sd = np.where(sd > 1e-12, sd, 1.0)
    return (tr - mu) / sd, (te - mu) / sd


def fit_logistic(
    x: np.ndarray, y: np.ndarray, *, l2: float = 1.0, iters: int = 3000, lr: float = 0.5
) -> tuple[np.ndarray, float]:
    """Plain L2-regularised logistic regression by gradient descent.

    ponytail: numpy, not a new dependency. Features are standardised by
    the caller so a fixed step size converges; this is a measurement tool,
    not a production trainer, and the question it answers ("is there any
    linear signal here") does not need a solver.
    """
    n, d = x.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(x @ w + b, -30, 30)))
        err = p - y
        w -= lr * ((x.T @ err) / n + l2 * w / n)
        b -= lr * float(err.mean())
    return w, b


def predict(x: np.ndarray, w: np.ndarray, b: float) -> np.ndarray:
    p: np.ndarray = 1.0 / (1.0 + np.exp(-np.clip(x @ w + b, -30, 30)))
    return p


def auc(y: np.ndarray, score: np.ndarray) -> float:
    """Rank-based AUC with average ranks for ties. Accuracy is useless at
    a ~32% base rate (always-"no" scores 68%); the only question that
    matters is whether the score ranks winners above losers."""
    n1 = float((y == 1).sum())
    n0 = float((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1, dtype=float)
    # average ranks within tied groups
    s_sorted = score[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def _lift_top_decile(y: np.ndarray, score: np.ndarray) -> float:
    """Hit rate among the highest-scoring 10%, divided by the base rate.
    The practical question: if this gated entries, would the ones it let
    through be better than average?"""
    base = float(y.mean())
    if base <= 0 or len(y) < 10:
        return float("nan")
    k = max(1, len(y) // 10)
    top = np.argsort(score, kind="mergesort")[-k:]
    return float(y[top].mean() / base)


def evaluate(
    data: Dataset, *, n_folds: int = 5, embargo: datetime.timedelta = DEFAULT_EMBARGO
) -> MetalabelReport:
    """Purged, embargoed walk-forward. Returns a report; draws no
    conclusion and wires nothing."""
    if len(data) == 0:
        return MetalabelReport(0, data.feature_names, float("nan"), [], {})

    index_of = {id(s): i for i, s in enumerate(data.signals)}
    folds = walk_forward_splits(
        data.signals, n_folds=n_folds, embargo=embargo, bar_days=_BAR_DAYS
    )

    results: list[FoldResult] = []
    for fold in folds:
        tr_idx = np.array([index_of[id(s)] for s in fold.train])
        te_idx = np.array([index_of[id(s)] for s in fold.test])
        y_tr, y_te = data.y[tr_idx], data.y[te_idx]
        if len(np.unique(y_tr)) < 2 or len(np.unique(y_te)) < 2:
            continue  # a fold with one class teaches/scores nothing
        x_tr, x_te = _standardise(data.x[tr_idx], data.x[te_idx])
        w, b = fit_logistic(x_tr, y_tr)
        s_te = predict(x_te, w, b)
        results.append(
            FoldResult(
                test_start=fold.test_start,
                n_train=len(tr_idx),
                n_test=len(te_idx),
                base_rate=float(y_te.mean()),
                auc=auc(y_te, s_te),
                lift_top_decile=_lift_top_decile(y_te, s_te),
            )
        )

    # Full-sample refit for coefficient inspection ONLY. These are not
    # out-of-sample evidence and must never be read as such — they exist to
    # show which features the model leans on, so a nonsense lean is visible.
    x_all, _ = _standardise(data.x, data.x)
    w_all, _b = fit_logistic(x_all, data.y)
    coeffs = dict(zip(data.feature_names, (float(v) for v in w_all), strict=True))

    return MetalabelReport(
        n=len(data),
        feature_names=data.feature_names,
        base_rate=float(data.y.mean()),
        folds=results,
        coefficients=coeffs,
    )


def permutation_null(
    data: Dataset,
    *,
    n_permutations: int = 20,
    n_folds: int = 5,
    embargo: datetime.timedelta = DEFAULT_EMBARGO,
    seed: int = 0,
) -> list[float]:
    """Re-run the WHOLE evaluation with the labels shuffled, `n` times.

    This is what makes a marginal AUC interpretable, and without it a
    number like 0.526 cannot be told apart from a biased harness. Shuffling
    `y` destroys any feature-label relationship while leaving everything
    else — the overlap between neighbouring signals, the fold boundaries,
    the class balance, the imputation — exactly as it was. So the resulting
    distribution is what this pipeline scores when there is *provably*
    nothing to find.

    If the real AUC sits inside that distribution, the honest reading is
    "no edge detected", however far above 0.500 it happens to look.
    Returns the mean AUC of each permutation.
    """
    rng = np.random.default_rng(seed)
    nulls: list[float] = []
    for _ in range(n_permutations):
        shuffled = dataclasses.replace(data, y=rng.permutation(data.y))
        report = evaluate(shuffled, n_folds=n_folds, embargo=embargo)
        if report.folds:
            nulls.append(report.mean_auc)
    return nulls


def univariate_lift(data: Dataset, *, n_bins: int = 5) -> dict[str, list[tuple[int, float]]]:
    """Per-feature target-hit rate by quantile bin — the same shape every
    detector in this system has been judged on (§19b/§19c), so a candidate
    feature is comparable with the incumbents rather than scored on its own
    flattering metric. Returns {feature: [(n, hit_rate), ...]} low to high.
    """
    out: dict[str, list[tuple[int, float]]] = {}
    for j, name in enumerate(data.feature_names):
        col = data.x[:, j]
        ok = np.isfinite(col)
        if ok.sum() < n_bins * 10:
            continue
        vals, ys = col[ok], data.y[ok]
        edges = np.quantile(vals, np.linspace(0, 1, n_bins + 1)[1:-1])
        bins = np.digitize(vals, edges)
        cells: list[tuple[int, float]] = []
        for b in range(n_bins):
            sel = bins == b
            rate = float(ys[sel].mean()) if sel.any() else float("nan")
            cells.append((int(sel.sum()), rate))
        out[name] = cells
    return out
