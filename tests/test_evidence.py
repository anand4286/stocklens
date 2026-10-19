from __future__ import annotations

import json
import math
import random
import statistics
import time
from dataclasses import replace
from datetime import date

import pytest

from stocklens.data.demo import DemoPrices
from stocklens.evidence import test_families as run_lab
from stocklens.evidence import walkforward as wf

TODAY = date(2026, 10, 16)
SYMBOLS = ("AAPL", "MSFT", "XOM")


@pytest.fixture(scope="module")
def demo():
    prices = DemoPrices(today=TODAY)
    return prices, prices.daily_bars("SPY", 756)


@pytest.fixture(scope="module")
def report(demo):
    prices, bench = demo
    return run_lab(prices.daily_bars("AAPL", 756), bench)


def _rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311


# ------------------------------------------------------------- point in time


def test_scores_use_only_the_past(demo):
    prices, bench = demo
    bars = prices.daily_bars("NVDA", 420)
    cut = 360
    future = [replace(b, close=b.close * (2.0 + i % 3), volume=b.volume * 5)
              for i, b in enumerate(bars[cut:])]
    mutated = bars[:cut] + future
    bench_mut = bench[:-60] + [replace(b, close=b.close * 0.3) for b in bench[-60:]]

    before = wf.score_series(bars, bench[-420:])
    after = wf.score_series(mutated, bench_mut[-420:])
    for name in before:
        assert before[name][:cut] == after[name][:cut], name
        assert before[name][cut:] != after[name][cut:], name

    closes = [b.close for b in bars]
    closes_mut = [b.close for b in mutated]
    assert wf.regime_series(closes)[:cut] == wf.regime_series(closes_mut)[:cut]


def test_forward_return_is_close_to_close():
    fwd = wf.forward_returns([100, 110, 121, 133.1], 2)
    assert fwd[0] == pytest.approx(0.21)
    assert fwd[2] is None and fwd[3] is None


# ------------------------------------------------------------- null behaviour


def test_permutation_p_is_roughly_uniform_under_the_null():
    rng = _rng(7)
    ps = []
    for _ in range(200):
        xs = [rng.gauss(0, 1) for _ in range(40)]
        ys = [rng.gauss(0, 1) for _ in range(40)]
        ps.append(wf.permutation_p(xs, ys, trials=199, seed=rng.randrange(10**6)))
    assert 0.4 < statistics.fmean(ps) < 0.6
    assert 0.01 <= sum(p < 0.05 for p in ps) / len(ps) <= 0.10


def test_random_walk_shows_no_evidence(demo):
    prices, bench = demo
    ps = []
    for symbol in SYMBOLS:
        out = run_lab(prices.daily_bars(symbol, 756), bench)
        for fam in out["families"]:
            assert not fam["works"], fam["verdict"]
            assert not fam["verdict"].startswith("Evidence against"), fam["verdict"]
            ps += [h["permutation_p"] for h in fam["horizons"].values()]
    assert statistics.fmean(ps) > 0.25


def test_too_little_history_returns_none(demo):
    prices, bench = demo
    assert run_lab(prices.daily_bars("AAPL", 299), bench) is None


# ------------------------------------------------------------- planted signal


def _planted(n: int = 500, edge: float = 0.4, seed: int = 3):
    rng = _rng(seed)
    scores = [max(-1.0, min(1.0, rng.gauss(0, 0.5))) for _ in range(n)]
    fwd = [edge * 0.02 * s + rng.gauss(0, 0.02) for s in scores]
    return scores, fwd


def test_planted_signal_is_detected():
    scores, fwd = _planted()
    stats = wf.horizon_stats(scores, fwd, h=1, trials=999)
    assert stats["t_stat"] > 3
    assert stats["permutation_p"] < 0.003
    assert stats["shift_p"] < 0.003

    pos = wf.positions(scores[:-1], "long_short")
    rule = wf.rule_stats(pos, fwd[:-1], cost_bps=0, n_trials=16)
    assert rule["dsr"] > 0.95
    rules = {"long_flat": rule, "long_short": rule}
    verdict, works = wf._verdict("a planted signal", {"1": stats}, rules, alpha=0.05 / 16)
    assert works and verdict.startswith("A planted signal works here")


def test_inverted_planted_signal_is_not_called_working():
    scores, fwd = _planted()
    stats = wf.horizon_stats([-s for s in scores], fwd, h=1, trials=999)
    assert stats["t_stat"] < -3
    pos = wf.positions([-s for s in scores[:-1]], "long_short")
    rule = wf.rule_stats(pos, fwd[:-1], cost_bps=0, n_trials=16)
    inverted = wf.rule_stats([-p for p in pos], fwd[:-1], cost_bps=0, n_trials=16)
    rule["dsr_inverted"] = inverted["dsr"]
    verdict, works = wf._verdict("x", {"1": stats}, {"long_short": rule}, alpha=0.05 / 16)
    assert not works and verdict.startswith("Evidence against")


def test_noise_is_not_detected():
    rng = _rng(11)
    scores = [rng.uniform(-1, 1) for _ in range(500)]
    fwd = [rng.gauss(0, 0.02) for _ in range(500)]
    stats = wf.horizon_stats(scores, fwd, h=1, trials=999)
    assert stats["permutation_p"] > 0.01
    assert abs(stats["t_stat"]) < 2.5


# ------------------------------------------------------------- economics


def test_costs_reduce_returns_monotonically():
    scores, fwd = _planted(edge=0.1)
    pos = wf.positions(scores[:-1], "long_short")
    returns = [wf.rule_stats(pos, fwd[:-1], c, 1)["annual_return"] for c in (0, 5, 10, 50, 100)]
    assert all(a > b for a, b in zip(returns, returns[1:], strict=False))


def test_rule_charges_cost_per_unit_of_change():
    net, turnover = wf.rule_returns([1, 1, -1, 0], [0.0, 0.0, 0.0, 0.0], cost_bps=10)
    assert turnover == 4  # enter 1, hold, flip 2, close 1
    assert net == pytest.approx([-0.001, 0.0, -0.002, -0.001])


def test_dsr_never_exceeds_psr(report):
    for fam in report["families"]:
        for rule in fam["rules"].values():
            if rule["dsr"] is not None:
                assert rule["dsr"] <= rule["psr"]
            assert rule["n_trials"] == report["tests_run"]


# ------------------------------------------------------------- shape


def test_bands_sum_to_observations(report):
    for fam in report["families"]:
        for h, block in fam["bands"].items():
            total = sum(b["n"] for b in block["bands"])
            assert total == block["n"] == fam["horizons"][h]["observations"]
            assert [b["band"] for b in block["bands"]] == [
                "strong_down", "mild_down", "neutral", "mild_up", "strong_up"]


def test_report_shape(report):
    assert report["horizons"] == [5, 21]
    assert report["tests_run"] == 4 * 2 * 2
    assert report["bonferroni_alpha"] == pytest.approx(0.05 / 16, abs=1e-6)
    assert report["permutation_trials"] >= math.ceil(1 / report["bonferroni_alpha"])
    assert {f["name"] for f in report["families"]} == {
        "technical", "momentum", "relative_strength", "price_composite"}
    curves = report["equity_curves"]
    assert len(curves["dates"]) <= 260
    for key, series in curves.items():
        assert len(series) == len(curves["dates"]), key
    assert "price_composite_long_flat" in curves
    for fam in report["families"]:
        assert set(fam["horizons"]) == {"5", "21"}
        assert set(fam["rules"]) == {"long_flat", "long_short"}
        assert set(fam["by_regime"]) == {"trending", "mean_reverting", "random"}
        assert sum(r["days"] for r in fam["by_regime"].values()) == report["observations"]
        assert sum(y["horizons"]["5"]["n"] for y in fam["by_year"]) == (
            fam["horizons"]["5"]["observations"])
        assert isinstance(fam["verdict"], str) and fam["verdict"]
    json.dumps(report)


def test_without_benchmark_skips_relative_strength(demo):
    prices, _ = demo
    out = run_lab(prices.daily_bars("KO", 400), None)
    assert {f["name"] for f in out["families"]} == {"technical", "momentum", "price_composite"}
    assert out["tests_run"] == 3 * 2 * 2


def test_runtime_budget(demo):
    prices, bench = demo
    bars = prices.daily_bars("TSLA", 756)
    start = time.perf_counter()
    run_lab(bars, bench)
    assert time.perf_counter() - start < 2.0
