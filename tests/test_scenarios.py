import math
import random
import time

from stocklens.data.demo import DemoPrices
from stocklens.quant.scenarios import shocks, simulate


def closes_from(returns, start=100.0):
    out = [start]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def garch_returns(n=1000, seed=1, fat=True):
    """GARCH(1,1) with a fat-tailed (normal mixture) innovation."""
    rng = random.Random(seed)  # noqa: S311
    omega, alpha, beta = 0.0001**2 * 20, 0.08, 0.9
    var, out = 0.0002, []
    for _ in range(n):
        z = rng.gauss(0, 1)
        if fat and rng.random() < 0.05:
            z *= 4
        r = math.sqrt(var) * z / (math.sqrt(0.95 + 0.05 * 16) if fat else 1)
        out.append(r)
        var = omega + alpha * r * r + beta * var
    return out


def test_percentiles_are_ordered_and_the_normal_model_is_alongside():
    r = simulate(closes_from(garch_returns()))
    assert [h["sessions"] for h in r["horizons"]] == [5, 21, 63]
    for h in r["horizons"]:
        for model in ("fhs", "normal"):
            v = h[model]
            assert v["p5"] < v["p25"] < v["p50"] < v["p75"] < v["p95"]
            assert 0 <= v["p_drop_10"] <= 1 and 0 <= v["p_gain_10"] <= 1
            assert v["expected_shortfall_95"] > 0
        assert h["tail_ratio"] is not None
    # Wider with horizon.
    spreads = [h["fhs"]["p95"] - h["fhs"]["p5"] for h in r["horizons"]]
    assert spreads == sorted(spreads)
    fan = r["fan"]
    assert len(fan) == 63 and fan[0]["session"] == 1 and fan[-1]["session"] == 63
    assert all(f["p5"] <= f["p50"] <= f["p95"] for f in fan)
    assert fan[-1]["p5"] == r["horizons"][-1]["fhs"]["p5"]
    assert r["reading"]


def test_fat_tailed_residuals_give_a_heavier_short_horizon_tail_than_normal():
    r = simulate(closes_from(garch_returns(n=1500, seed=4)))
    assert r["residual_excess_kurtosis"] > 1
    assert r["horizons"][0]["tail_ratio"] > 1.0


def test_deterministic_with_seed_and_different_without():
    closes = closes_from(garch_returns())
    assert simulate(closes, seed=3) == simulate(closes, seed=3)
    assert simulate(closes, seed=3)["fan"] != simulate(closes, seed=4)["fan"]


def test_budget_and_floors():
    closes = [b.close for b in DemoPrices().daily_bars("AAPL", 756)]
    start = time.perf_counter()
    r = simulate(closes, paths=2000)
    assert time.perf_counter() - start < 1.5
    assert r["paths"] == 2000
    assert simulate(closes[:200]) is None
    assert simulate(closes, horizons=(250,), paths=5000)["paths"] == 800  # draw cap


def test_shocks_are_monotone_in_the_market_move_for_positive_beta():
    rng = random.Random(2)  # noqa: S311
    m = [rng.gauss(0.0003, 0.012) for _ in range(1200)]
    y = [1.4 * x + rng.gauss(0, 0.01) for x in m]
    rows = shocks(closes_from(y), closes_from(m))
    assert [row["market_move"] for row in rows] == [-0.20, -0.10, -0.05, 0.05, 0.10]
    expected = [row["expected"] for row in rows]
    assert expected == sorted(expected)
    for row in rows:
        assert row["low"] < row["expected"] < row["high"]
        assert abs(row["beta_used"] - 1.4) < 0.15
        assert row["beta_kind"] == ("downside" if row["market_move"] < 0 else "upside")
        assert row["analog"]["n"] >= 0 and row["reading"]
        assert row["sector_expected"] is None
    # The historical analog agrees in sign with the model where the sample has data.
    five = next(r for r in rows if r["market_move"] == 0.05)
    assert five["analog"]["n"] > 10 and five["analog"]["median"] > 0


def test_shocks_with_a_sector_and_short_samples():
    p = DemoPrices()
    stock = [b.close for b in p.daily_bars("XOM", 756)]
    spy = [b.close for b in p.daily_bars("SPY", 756)]
    xle = [b.close for b in p.daily_bars("XLE", 756)]
    rows = shocks(stock, spy, xle)
    assert all(r["sector_expected"] is not None for r in rows)
    assert shocks(stock[:50], spy[:50]) == []
