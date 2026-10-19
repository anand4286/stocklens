from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from stocklens.data.base import Bar
from stocklens.quant import similarity as sim

START = date(2015, 1, 1)


def _bars(closes):
    return [Bar(START + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e6)
            for i, c in enumerate(closes)]


def _walk(n, seed):
    rng = random.Random(seed)  # noqa: S311 - seeded test data
    px, out = 100.0, []
    for _ in range(n):
        px *= math.exp(rng.gauss(0, 0.015))
        out.append(px)
    return out


def test_znormalise_flat_window_is_none():
    assert sim.znormalise([5.0] * 30) is None
    z = sim.znormalise([1.0, 2.0, 3.0])
    assert sum(z) == pytest.approx(0)
    assert math.sqrt(sum(v * v for v in z) / 3) == pytest.approx(1)


def test_too_short_history_is_none():
    assert sim.analogs(_bars(_walk(200, 1)), window=30, horizon=21, k=10) is None


def test_flat_query_is_none():
    closes = _walk(800, 2) + [50.0] * 30
    assert sim.analogs(_bars(closes)) is None


def test_exact_repeat_is_nearest_with_zero_distance():
    closes = _walk(1000, 3)
    pattern = closes[300:330]
    # Append the same shape at a different price level as the query.
    scale = closes[-1] / pattern[0] * 1.3
    closes = closes + [p * scale for p in pattern]
    out = sim.analogs(_bars(closes), window=30, horizon=21, k=10)
    best = out["matches"][0]
    assert best["distance"] == pytest.approx(0, abs=1e-6)
    assert best["start"] == (START + timedelta(days=300)).isoformat()


def test_exclusion_zone_respected():
    bars = _bars(_walk(1500, 4))
    out = sim.analogs(bars, window=30, horizon=21, k=10)
    index = {b.day.isoformat(): i for i, b in enumerate(bars)}
    starts = [index[m["start"]] for m in out["matches"]]
    q = index[out["query"]["start"]]
    assert len(starts) == 10
    for i, a in enumerate(starts):
        assert q - a >= 30
        assert a + 29 + 21 <= len(bars) - 1
        for b in starts[i + 1:]:
            assert abs(a - b) >= 30
    dists = [m["distance"] for m in out["matches"]]
    assert dists == sorted(dists)


def test_base_rate_computed_correctly():
    closes = _walk(900, 5)
    out = sim.analogs(_bars(closes), window=30, horizon=21, k=10)
    rets = [closes[i + 21] / closes[i] - 1 for i in range(len(closes) - 21)]
    assert out["base_rate"]["n"] == len(rets)
    assert out["base_rate"]["mean_forward"] == pytest.approx(sum(rets) / len(rets), abs=1e-4)
    assert out["base_rate"]["share_up"] == pytest.approx(
        sum(r > 0 for r in rets) / len(rets), abs=1e-4)
    assert out["edge"] == pytest.approx(
        out["summary"]["mean_forward"] - out["base_rate"]["mean_forward"], abs=2e-4)


def test_random_walk_is_not_significant():
    out = sim.analogs(_bars(_walk(2500, 42)), window=30, horizon=21, k=10)
    assert out["significant"] is False
    assert "no information" in out["verdict"]
    assert "not evidence" in out["warning"]
    assert out["summary"]["n"] == 10
    assert len(out["query"]["path"]) == 30
