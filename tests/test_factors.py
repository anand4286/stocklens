import math
import random

from stocklens.quant.factors import (
    aligned_returns,
    attribution,
    newey_west_lag,
    ols,
    rolling_beta,
    tail_dependence,
)


def closes_from(returns, start=100.0):
    out = [start]
    for r in returns:
        out.append(out[-1] * math.exp(r))
    return out


def synthetic(n=1000, seed=3, b_m=1.3, b_s=0.7, alpha=0.0002, phi=0.0):
    rng = random.Random(seed)  # noqa: S311
    m = [rng.gauss(0, 0.01) for _ in range(n)]
    u = [rng.gauss(0, 0.008) for _ in range(n)]
    s = [0.9 * a + b for a, b in zip(m, u, strict=True)]
    e, prev = [], 0.0
    for _ in range(n):
        prev = phi * prev + rng.gauss(0, 0.012)
        e.append(prev)
    y = [alpha + b_m * a + b_s * b + c for a, b, c in zip(m, u, e, strict=True)]
    dates = [f"d{i:05d}" for i in range(n + 1)]
    return closes_from(y), closes_from(m), closes_from(s), dates


def test_recovers_known_betas_on_synthetic_factors():
    stock, market, sector, dates = synthetic()
    r = attribution(stock, market, sector, dates)
    assert abs(r["market_beta"]["value"] - 1.3) < 0.08
    assert abs(r["sector_beta"]["value"] - 0.7) < 0.1
    assert abs(r["sector_market_beta"] - 0.9) < 0.05
    assert r["observations"] == 1000 and r["has_sector"]
    assert r["nw_lag"] == newey_west_lag(1000) == 6
    assert 0 < r["r_squared"] < 1
    assert "market" in r["reading"]


def test_variance_shares_sum_to_one_and_match_r_squared():
    stock, market, sector, dates = synthetic()
    r = attribution(stock, market, sector, dates)
    shares = r["variance_share"]
    assert abs(sum(shares.values()) - 1) < 1e-3
    assert abs(shares["market"] + shares["sector"] - r["r_squared"]) < 2e-3


def test_one_factor_model_without_a_sector():
    stock, market, _, dates = synthetic()
    r = attribution(stock, market, None, dates)
    assert r["sector_beta"] is None and r["variance_share"]["sector"] is None
    assert abs(r["variance_share"]["market"] + r["variance_share"]["idiosyncratic"] - 1) < 1e-3
    assert r["decomposition"]["sector"] is None


def test_decomposition_sums_to_the_total_log_return():
    stock, market, sector, dates = synthetic()
    d = attribution(stock, market, sector, dates)["decomposition"]
    assert d["sessions"] == 252
    assert abs(d["market"] + d["sector"] + d["specific"] - d["total"]) < 1e-5
    assert abs(d["total"] - math.log(stock[-1] / stock[-253])) < 1e-5
    last = d["series"][-1]
    assert abs(last["total"] - d["total"]) < 1e-9


def test_newey_west_widens_the_alpha_error_under_autocorrelated_residuals():
    stock, market, sector, dates = synthetic(phi=0.6, alpha=0.001)
    r = attribution(stock, market, sector, dates)
    assert abs(r["alpha_t_nw"]) < abs(r["alpha_t_ols"])
    _, rets = aligned_returns(stock, market)
    fit = ols(rets[0], [rets[1]])
    assert fit["se_nw"][0] > fit["se_ols"][0] * 1.3


def test_newey_west_close_to_ols_when_residuals_are_independent():
    _, rets = aligned_returns(*synthetic(n=3000)[:2])
    fit = ols(rets[0], [rets[1]])
    assert 0.8 < fit["se_nw"][1] / fit["se_ols"][1] < 1.2


def test_tail_dependence_is_the_base_rate_for_independent_series():
    rng = random.Random(11)  # noqa: S311
    a = [rng.gauss(0, 1) for _ in range(20000)]
    b = [rng.gauss(0, 1) for _ in range(20000)]
    td = tail_dependence(a, b)
    assert abs(td["probability"] - 0.05) < 0.015
    assert tail_dependence(a, a)["probability"] == 1.0


def test_asymmetric_betas_and_rolling_series():
    rng = random.Random(5)  # noqa: S311
    m = [rng.gauss(0, 0.01) for _ in range(800)]
    y = [(0.6 if x > 0 else 1.6) * x + rng.gauss(0, 0.005) for x in m]
    r = attribution(closes_from(y), closes_from(m), None, [str(i) for i in range(801)])
    assert r["asymmetry"]["downside_beta"] > r["asymmetry"]["upside_beta"] + 0.6
    pts = r["rolling_beta"]["points"]
    assert len(pts) <= 260 and pts[-1]["date"] == "800"
    assert len(rolling_beta(y, m, 63)) == len(y) - 62


def test_rolling_beta_matches_direct_ols():
    rng = random.Random(9)  # noqa: S311
    m = [rng.gauss(0, 0.01) for _ in range(100)]
    y = [1.1 * x + rng.gauss(0, 0.004) for x in m]
    direct = ols(y[-63:], [m[-63:]])["coef"][1]
    assert abs(rolling_beta(y, m, 63)[-1] - direct) < 1e-9


def test_short_or_misaligned_samples():
    stock, market, sector, dates = synthetic(n=100)
    assert attribution(stock, market, sector, dates) is None
    stock, market, sector, dates = synthetic(n=400)
    # Tail alignment: a longer market history is cut to the stock's.
    r = attribution(stock[-200:], market, sector, dates)
    assert r["observations"] == 199 and r["end"] == dates[-1]


def test_demo_sector_funds_carry_realistic_structure():
    from stocklens.data.demo import DemoPrices, sector_of
    from stocklens.data.sectors import etf_for

    p = DemoPrices()

    def closes(s):
        return [b.close for b in p.daily_bars(s, 756)]

    dates = [b.day.isoformat() for b in p.daily_bars("SPY", 756)]
    xlu = attribution(closes("XLU"), closes("SPY"), None, dates)
    xlk = attribution(closes("XLK"), closes("SPY"), None, dates)
    assert xlu["market_beta"]["value"] < 0.7 < 1.0 < xlk["market_beta"]["value"]
    # An ordinary demo stock loads on its own sector fund, beyond the market.
    stock = "ACME"
    r = attribution(closes(stock), closes("SPY"), closes(etf_for(sector_of(stock))), dates)
    assert r["sector_beta"]["value"] > 0.2 and r["sector_beta"]["t_nw"] > 2
