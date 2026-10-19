from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from stocklens.data.base import Bar
from stocklens.quant import volume_profile as vp

START = date(2023, 1, 2)


def _bars(closes, spread=0.01, volume=1_000_000.0):
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(START + timedelta(days=i), c, c * (1 + spread), c * (1 - spread), c,
                       volume * (1 + (i % 7) / 10)))
    return out


def _walk(n, seed=7, start=100.0):
    rng = random.Random(seed)  # noqa: S311 - seeded test data
    px, out = start, []
    for _ in range(n):
        px *= math.exp(rng.gauss(0, 0.015))
        out.append(px)
    return out


def test_none_below_floor():
    assert vp.build(_bars(_walk(59))) is None


def test_volume_is_conserved():
    bars = _bars(_walk(300))
    prof = vp.build(bars, lookback=252)
    assert prof is not None
    expected = sum(b.volume for b in bars[-252:])
    assert sum(b["volume"] for b in prof["bins"]) == pytest.approx(expected, rel=1e-9)
    assert sum(b["share"] for b in prof["bins"]) == pytest.approx(1.0, abs=1e-3)
    assert len(prof["bins"]) == vp.BINS


def test_poc_is_the_heaviest_bin_and_inside_the_value_area():
    prof = vp.build(_bars(_walk(252, seed=3)))
    heaviest = max(prof["bins"], key=lambda b: b["volume"])
    assert heaviest["low"] <= prof["poc"] <= heaviest["high"]
    assert prof["value_area_low"] <= prof["poc"] <= prof["value_area_high"]
    inside = [b for b in prof["bins"] if b["in_value_area"]]
    assert sum(b["share"] for b in inside) >= 0.7 - 1e-3
    assert heaviest["in_value_area"]
    # Contiguous band.
    flags = [b["in_value_area"] for b in prof["bins"]]
    first, last = flags.index(True), len(flags) - 1 - flags[::-1].index(True)
    assert all(flags[first:last + 1])


def test_position_classification():
    base = [100.0 + math.sin(i / 5) for i in range(120)]
    assert vp.build(_bars(base + [100.0]))["position"] == "inside_value"
    assert vp.build(_bars(base + [103.0]))["position"] == "above_value"
    assert vp.build(_bars(base + [97.0]))["position"] == "below_value"
    prof = vp.build(_bars(base + [103.0]))
    assert prof["distance_to_poc_pct"] == pytest.approx(103.0 / prof["poc"] - 1, abs=1e-4)


def test_nodes_found_on_a_two_shelf_profile():
    # Price spends time at 100 and at 120, passes quickly through 110.
    closes = [100.0] * 80 + [100 + i for i in range(1, 20)] + [120.0] * 80
    bars = [Bar(START + timedelta(days=i), c, c + 0.5, c - 0.5, c, 1e6)
            for i, c in enumerate(closes)]
    prof = vp.build(bars)
    assert any(abs(p - 100) < 2 for p in prof["hvn"])
    assert any(abs(p - 120) < 2 for p in prof["hvn"])
    assert any(105 < p < 115 for p in prof["lvn"])


def test_flat_price_has_no_profile():
    bars = [Bar(START + timedelta(days=i), 50, 50, 50, 50, 1e6) for i in range(100)]
    assert vp.build(bars) is None


def test_level_test_returns_sane_numbers():
    prof = vp.build(_bars(_walk(252, seed=11)))
    lt = prof["level_test"]
    assert lt is not None
    assert 0 <= lt["reactions"] <= lt["touches"]
    for key in ("reaction_rate", "base_rate"):
        assert lt[key] is None or 0 <= lt[key] <= 1
    assert isinstance(lt["verdict"], str) and lt["verdict"]
    # Deterministic: same input, same answer.
    assert vp.build(_bars(_walk(252, seed=11)))["level_test"] == lt


def test_no_nan_or_inf():
    prof = vp.build(_bars(_walk(400, seed=5)))

    def walk(x):
        if isinstance(x, float):
            assert math.isfinite(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(prof)
