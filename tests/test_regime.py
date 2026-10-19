# ruff: noqa: S311 -- seeded simulation, not cryptography.
import math
import random

import pytest

from stocklens.quant import regime


def _walk(seed: int, n: int, phi: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    prices, prev = [100.0], 0.0
    for _ in range(n):
        r = phi * prev + rng.gauss(0, 0.01)
        prev = r
        prices.append(prices[-1] * math.exp(r))
    return prices


def test_random_walk_is_random():
    calls = [regime.classify(_walk(seed, 750))["trend"] for seed in range(20)]
    assert calls.count("random") >= 19


def test_strongly_autocorrelated_returns_are_trending():
    out = regime.classify(_walk(2, 1000, phi=0.6))
    assert out["trend"] == "trending"
    assert out["agree"] is True
    assert out["hurst"] > regime.HURST_NULL_MEAN and out["variance_ratio"] > 1
    assert out["multipliers"]["momentum"] > 1 > out["multipliers"]["technical"]


def test_anti_persistent_returns_are_mean_reverting():
    assert regime.classify(_walk(3, 1000, phi=-0.6))["trend"] == "mean_reverting"


def test_short_history_is_random_with_unknown_volatility():
    out = regime.classify(_walk(1, 40))
    assert out["trend"] == "random"
    assert out["volatility"] == "unknown"
    assert out["hurst"] is None and out["vol_percentile"] is None
    assert out["agree"] is False


def test_volatility_regime_reads_against_own_history():
    rng = random.Random(4)
    rets = [rng.gauss(0, 0.01) for _ in range(300)] + [rng.gauss(0, 0.04) for _ in range(25)]
    prices = [100.0]
    for r in rets:
        prices.append(prices[-1] * math.exp(r))
    out = regime.classify(prices)
    assert out["volatility"] == "high"
    assert out["vol_percentile"] > 0.8


def test_tilt_preserves_total_and_direction():
    weights = {
        "technical": 0.2,
        "momentum": 0.2,
        "relative_strength": 0.1,
        "fundamental": 0.2,
        "valuation": 0.2,
        "sentiment": 0.1,
    }
    total = sum(weights.values())
    trending = regime.tilt_weights(weights, {"trend": "trending"})
    reverting = regime.tilt_weights(weights, {"trend": "mean_reverting"})
    assert sum(trending.values()) == pytest.approx(total)
    assert sum(reverting.values()) == pytest.approx(total)
    assert trending["momentum"] > weights["momentum"] > reverting["momentum"]
    assert trending["technical"] < weights["technical"] < reverting["technical"]
    assert regime.tilt_weights(weights, {"trend": "random"}) == pytest.approx(weights)
    scaled = regime.tilt_weights({k: 3 * v for k, v in weights.items()}, {"trend": "trending"})
    assert sum(scaled.values()) == pytest.approx(3 * total)


def test_multipliers_are_bounded():
    for trend in ("trending", "mean_reverting", "random"):
        for m in regime.multipliers_for(trend).values():
            assert regime.MULTIPLIER_BOUNDS[0] <= m <= regime.MULTIPLIER_BOUNDS[1]
