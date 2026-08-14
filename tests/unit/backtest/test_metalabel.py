"""The meta-label evaluator's own mechanics.

These matter more than usual: this module exists to answer "is there
edge", and a broken AUC or a leaking standardiser would answer "yes"
regardless of the data. Every test here is about the measurement being
trustworthy, not about the model being good.
"""

import numpy as np

from kairodex.backtest.metalabel import (
    _lift_top_decile,
    _standardise,
    auc,
    fit_logistic,
    predict,
)


def test_auc_is_one_for_a_perfect_ranking():
    y = np.array([0, 0, 1, 1])
    assert auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0


def test_auc_is_zero_for_a_perfectly_inverted_ranking():
    y = np.array([0, 0, 1, 1])
    assert auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0


def test_auc_is_half_when_every_score_is_tied():
    """The degenerate case that must NOT look like edge. A model that
    outputs a constant has no ranking ability, and average-rank tie
    handling is what makes that come out at exactly 0.5 rather than
    something flattering."""
    y = np.array([0, 1, 0, 1, 1, 0])
    assert auc(y, np.full(6, 0.42)) == 0.5


def test_auc_matches_the_probabilistic_definition_on_a_known_case():
    """AUC = P(score of a random positive > score of a random negative),
    ties counting half. Two positives, two negatives, one tie: pairs are
    (1.0>0.5)=1, (1.0>0.9)=1, (0.9>0.5)=1, (0.9 vs 0.9)=0.5 -> 3.5/4."""
    y = np.array([0, 0, 1, 1])
    score = np.array([0.5, 0.9, 0.9, 1.0])
    assert auc(y, score) == 3.5 / 4


def test_auc_is_nan_when_one_class_is_absent():
    assert np.isnan(auc(np.zeros(5), np.arange(5.0)))


def test_standardise_uses_only_training_statistics():
    """The classic silent leak: scaling with statistics computed over
    train+test. The test fold must be transformed by the TRAIN mean/std,
    so a test column with a wildly different distribution does not come
    out centred on zero."""
    train = np.array([[0.0], [2.0]])  # mean 1, std 1
    test = np.array([[100.0]])
    tr, te = _standardise(train, test)
    assert tr.mean() == 0.0
    assert te[0, 0] == 99.0  # (100 - 1) / 1, NOT re-centred on itself


def test_standardise_imputes_missing_with_the_train_median():
    train = np.array([[1.0], [3.0], [np.nan]])
    test = np.array([[np.nan]])
    tr, te = _standardise(train, test)
    assert np.isfinite(tr).all()
    assert np.isfinite(te).all()
    # train median of {1, 3} is 2, which is also the train mean -> z = 0
    assert te[0, 0] == 0.0


def test_standardise_survives_a_constant_column():
    """Zero variance must not divide by zero and produce inf/nan, which
    would poison every downstream coefficient."""
    train = np.array([[5.0], [5.0], [5.0]])
    tr, te = _standardise(train, np.array([[5.0]]))
    assert np.isfinite(tr).all() and np.isfinite(te).all()


def test_logistic_learns_a_separable_signal():
    """Sanity floor: if the model cannot rank a trivially separable
    dataset, a ~0.5 AUC on real data would be uninformative about the
    data rather than about the market."""
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, 2))
    y = (x[:, 0] > 0).astype(int)
    w, b = fit_logistic(x, y)
    assert auc(y, predict(x, w, b)) > 0.95


def test_logistic_finds_nothing_in_pure_noise():
    """The result this module must be capable of returning. Labels
    independent of features -> AUC near chance, in-sample."""
    rng = np.random.default_rng(1)
    x = rng.normal(size=(600, 3))
    y = rng.integers(0, 2, size=600)
    w, b = fit_logistic(x, y)
    assert 0.40 < auc(y, predict(x, w, b)) < 0.60


def test_lift_is_one_when_the_score_is_uninformative():
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, size=1000)
    lift = _lift_top_decile(y, np.zeros(1000))
    assert 0.6 < lift < 1.4  # a constant score picks an arbitrary tenth


def test_lift_exceeds_one_when_the_score_ranks_winners_first():
    y = np.array([0] * 90 + [1] * 10)
    score = np.arange(100.0)  # positives are the top decile exactly
    assert _lift_top_decile(y, score) == 10.0
