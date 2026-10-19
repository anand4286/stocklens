# ruff: noqa: S311 -- seeded simulation, not cryptography.
import math
import random

import pytest

from stocklens.quant import volatility as vol


def _garch_returns(seed: int, n: int, omega: float, alpha: float, beta: float) -> list[float]:
    rng = random.Random(seed)
    var = omega / (1 - alpha - beta)
    out = []
    for _ in range(n):
        r = rng.gauss(0, 1) * math.sqrt(var)
        out.append(r)
        var = omega + alpha * r * r + beta * var
    return out


def _brownian_ohlc(seed: int, n: int, daily_sd: float = 0.01, steps: int = 200) -> dict:
    rng = random.Random(seed)
    o, h, lo, c = [], [], [], []
    px = 100.0
    step_sd = daily_sd / math.sqrt(steps)
    for _ in range(n):
        path = [px]
        for _ in range(steps):
            path.append(path[-1] * math.exp(rng.gauss(0, step_sd)))
        o.append(path[0])
        h.append(max(path))
        lo.append(min(path))
        c.append(path[-1])
        px = path[-1]
    return {"open": o, "high": h, "low": lo, "close": c}


def _prices(rets: list[float]) -> list[float]:
    p = [100.0]
    for r in rets:
        p.append(p[-1] * math.exp(r))
    return p


def test_garch_recovers_known_parameters():
    rets = _garch_returns(7, 2500, omega=2e-6, alpha=0.08, beta=0.90)
    fit = vol.fit_garch(rets)
    assert fit is not None
    assert fit.alpha == pytest.approx(0.08, abs=0.04)
    assert fit.beta == pytest.approx(0.90, abs=0.06)
    assert fit.persistence < 1
    assert fit.half_life == pytest.approx(math.log(0.5) / math.log(fit.persistence))


def test_garch_withholds_below_its_floor():
    assert vol.fit_garch(_garch_returns(1, 100, 2e-6, 0.08, 0.9)) is None
    assert vol.fit_garch([0.0] * 400) is None


def test_forecast_converges_to_long_run_vol():
    fit = vol.fit_garch(_garch_returns(11, 1500, 2e-6, 0.10, 0.85))
    assert fit is not None
    long_run = math.sqrt(fit.long_run_variance * vol.TRADING_DAYS)
    gaps = [abs(fit.forecast(h) - long_run) for h in (1, 5, 21, 63, 126, 2000)]
    assert gaps == sorted(gaps, reverse=True)
    assert fit.forecast(5000) == pytest.approx(long_run, rel=0.02)


def test_parkinson_and_garman_klass_track_close_to_close_on_brownian_bars():
    bars = _brownian_ohlc(3, 400)
    true_vol = 0.01 * math.sqrt(252)
    cc = vol.close_to_close_vol(bars["close"])
    pk = vol.parkinson_vol(bars["high"], bars["low"])
    gk = vol.garman_klass_vol(bars["open"], bars["high"], bars["low"], bars["close"])
    for est in (cc, pk, gk):
        assert est == pytest.approx(true_vol, rel=0.15)
    assert pk == pytest.approx(cc, rel=0.2)


def test_jarque_bera_rejects_fat_tails_and_not_gaussian():
    rng = random.Random(5)
    gaussian = [rng.gauss(0, 0.01) for _ in range(1000)]
    fat = [rng.gauss(0, 0.05 if rng.random() < 0.05 else 0.01) for _ in range(1000)]
    assert vol.jarque_bera(gaussian) < 5.99
    assert vol.jarque_bera(fat) > 5.99
    assert vol.excess_kurtosis(fat) > 3
    assert vol.jarque_bera([0.01] * 10) is None


def test_corrected_hurst_is_near_half_for_a_random_walk():
    values = []
    for seed in range(20):
        rng = random.Random(seed)
        values.append(vol.hurst_corrected(_prices([rng.gauss(0, 0.01) for _ in range(750)])))
    assert sum(values) / len(values) == pytest.approx(0.5, abs=0.05)
    assert vol.hurst_corrected([100.0] * 50) is None


def test_cone_places_current_vol_in_its_history():
    rng = random.Random(9)
    rets = [rng.gauss(0, 0.01) for _ in range(400)] + [rng.gauss(0, 0.04) for _ in range(30)]
    cone = {row["window"]: row for row in vol.vol_cone(rets)}
    assert cone[10]["p10"] <= cone[10]["p50"] <= cone[10]["p90"]
    assert cone[10]["percentile"] > 0.9
    short = vol.vol_cone(rets[:50])
    assert all(row["p50"] is None and row["percentile"] is None for row in short)


def test_summary_shape_and_floors():
    out = vol.summary(_brownian_ohlc(4, 400))
    assert set(out) == {
        "garch",
        "forecast",
        "range_estimators",
        "cone",
        "distribution",
        "hurst_corrected",
    }
    assert out["garch"] is not None and out["garch"]["observations"] == 399
    assert [f["sessions"] for f in out["forecast"]] == [5, 21, 63, 126]
    assert [c["window"] for c in out["cone"]] == [10, 21, 63]
    assert out["distribution"]["normal_rejected"] is False

    tiny = vol.summary(_brownian_ohlc(4, 15))
    assert tiny["garch"] is None and tiny["forecast"] == []
    assert set(tiny["range_estimators"].values()) == {None}
    assert tiny["distribution"]["normal_rejected"] is None
    assert tiny["hurst_corrected"] is None
