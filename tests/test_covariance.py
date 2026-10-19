from __future__ import annotations

import random

import pytest

from stocklens.quant import covariance as cv


def factor_returns(
    n_series: int, n_obs: int, beta: float = 1.0, seed: int = 7, spread: float = 0.0
) -> list[list[float]]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    market = [rng.gauss(0, 0.01) for _ in range(n_obs)]
    return [
        [(beta + spread * k) * m + rng.gauss(0, 0.01) for m in market] for k in range(n_series)
    ]


def test_align_trims_to_newest_common_window_and_drops_short():
    series = {"A": list(range(100)), "B": list(range(80)), "C": list(range(10))}
    symbols, obs = cv.align({k: [float(x) for x in v] for k, v in series.items()})
    assert symbols == ["A", "B"]
    assert len(obs[0]) == len(obs[1]) == 80
    assert obs[0][-1] == 99.0
    assert cv.align({"A": [1.0] * 100}) == ([], [])


def test_sample_covariance_and_correlation():
    obs = [[1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]]
    cov = cv.sample_covariance(obs)
    assert cov[0][0] == pytest.approx(5 / 3)
    assert cov[0][1] == pytest.approx(10 / 3)
    corr = cv.to_correlation(cov)
    assert corr[0][1] == pytest.approx(1.0)
    assert corr[0][0] == 1.0


def test_marchenko_pastur_edges_formula():
    lower, upper = cv.marchenko_pastur_edges(25, 100)
    assert lower == pytest.approx(0.25)
    assert upper == pytest.approx(2.25)
    assert cv.marchenko_pastur_edges(1, 0) == (0.0, 0.0)


def test_ledoit_wolf_intensity_in_unit_interval_and_shrinks_toward_target():
    obs = factor_returns(6, 120, beta=0.8)
    sample = cv.sample_covariance(obs)
    target = cv.constant_correlation_target(sample)
    shrunk, intensity = cv.ledoit_wolf_shrinkage(obs)
    assert 0.0 <= intensity <= 1.0
    assert intensity > 0.0

    def dist(a, b):
        return sum((a[i][j] - b[i][j]) ** 2 for i in range(6) for j in range(6))

    assert dist(shrunk, target) < dist(sample, target)
    for i in range(6):
        assert shrunk[i][i] == pytest.approx(sample[i][i])


def test_shrinkage_intensity_falls_with_more_data():
    # Heterogeneous betas: the truth is not constant-correlation, so more data
    # should earn less shrinkage.
    _, short = cv.ledoit_wolf_shrinkage(factor_returns(8, 80, 0.2, seed=3, spread=0.3))
    _, long = cv.ledoit_wolf_shrinkage(factor_returns(8, 2000, 0.2, seed=3, spread=0.3))
    assert 0.0 < long < short < 1.0


def test_shrinkage_saturates_when_target_is_the_truth():
    # Identical betas and noise: the true matrix is constant-correlation.
    _, intensity = cv.ledoit_wolf_shrinkage(factor_returns(8, 500, seed=3))
    assert intensity > 0.5


def test_spectrum_detects_one_factor():
    obs = factor_returns(10, 500, beta=1.0, seed=11)
    report = cv.spectrum([f"S{i}" for i in range(10)], obs)
    assert report.signal_factors == 1
    assert report.eigenvalues[0] > report.upper_edge
    assert 0.4 < report.first_factor_share < 0.7
    assert sum(report.explained) == pytest.approx(1.0)
    assert report.q == pytest.approx(10 / 500)


def test_spectrum_of_noise_is_inside_band():
    rng = random.Random(5)  # noqa: S311
    obs = [[rng.gauss(0, 1) for _ in range(1000)] for _ in range(5)]
    report = cv.spectrum(list("ABCDE"), obs)
    assert report.signal_factors == 0


def test_denoise_preserves_unit_diagonal_and_signal():
    obs = factor_returns(8, 300, seed=2)
    corr = cv.to_correlation(cv.sample_covariance(obs))
    clean = cv.denoise(corr, 300)
    for i in range(8):
        assert clean[i][i] == pytest.approx(1.0)
    off = [clean[i][j] for i in range(8) for j in range(8) if i != j]
    # Noise collapsed: off-diagonals cluster tightly around the common factor.
    assert max(off) - min(off) < 0.1
