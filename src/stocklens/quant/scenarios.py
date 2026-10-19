"""Where the price could be, and what a market shock would do to it.

Why not a normal cone
---------------------
`risk.range_cone` draws ±1σ and ±2σ from one constant volatility under a
normal distribution. Two things are wrong with that for a stock, and both
understate risk exactly where a reader needs it:

1. **Volatility clusters.** After a violent month the next month is likelier
   to be violent too. A constant σ uses the average year's volatility whether
   today is calm or turbulent.
2. **Daily returns are fat-tailed.** Even after volatility is accounted for,
   the standardised shocks have more mass in the tails than a Gaussian, so a
   normal model's 5th percentile is too close to today's price.

Filtered historical simulation
------------------------------
Barone-Adesi, Giannopoulos & Vosper (1999) fix both without assuming a shape:

1. Fit GARCH(1,1) (`volatility.fit_garch`) and divide each historical return by
   its own conditional volatility. What is left — the standardised residuals —
   is approximately i.i.d., but keeps the *empirical* shape: its skew, its fat
   tails, its crash days.
2. Simulate forward from **today's** conditional variance: draw a residual at
   random from history, scale it by the current σ, feed the resulting return
   back into the GARCH recursion to get tomorrow's σ, and repeat.

So a turbulent today starts the paths wide and lets them narrow at the speed
the fit estimates, and a 1987-style day in the sample can recur, at today's
volatility. The simulation uses zero drift on purpose: a year's sample mean is
mostly noise (its standard error is about σ/√T, the same size as the mean
itself), and projecting it forward would turn last year's trend into a
forecast.

Determinism and budget
----------------------
`random.Random(seed)`, so the same closes and seed give the same answer — a
shareable page must not change on refresh. 2000 paths × 63 steps is ~126k
draws and runs in roughly 0.1-0.2 s in pure Python.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from statistics import NormalDist

from stocklens.analytics.risk import log_returns
from stocklens.quant.factors import MIN_RETURNS, aligned_returns, asymmetric_beta, ols
from stocklens.quant.volatility import GARCH_MIN_OBSERVATIONS, excess_kurtosis, fit_garch

TRADING_DAYS = 252
PERCENTILES = (5, 25, 50, 75, 95)
FAN_MAX_POINTS = 63
#: Hard cap on paths × steps: the runtime budget, not a statistical choice.
MAX_DRAWS = 200_000
SHOCK_HORIZON = 21
ANALOG_TOLERANCE = 0.025
_N = NormalDist()


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _r(x: float | None, places: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, places)


def _quantile(ordered: Sequence[float], q: float) -> float:
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def standardised_residuals(returns: Sequence[float], omega: float, alpha: float,
                           beta: float) -> list[float]:
    """Each demeaned return over its GARCH conditional σ, rescaled to unit variance.

    The recursion is seeded exactly as `fit_garch` seeds it. Rescaling makes the
    bootstrap reproduce the fitted variance on average: without it, a residual
    set with sample variance 1.05 would inflate every simulated path by 2.5%.
    """
    mu = _mean(returns)
    rets = [r - mu for r in returns]
    var = sum(r * r for r in rets) / len(rets)
    z = []
    for r in rets:
        z.append(r / math.sqrt(var))
        var = omega + alpha * r * r + beta * var
    mz = _mean(z)
    sz = math.sqrt(sum((v - mz) ** 2 for v in z) / len(z))
    return [(v - mz) / sz for v in z]


def _horizon_stats(price: float, log_moves: list[float]) -> dict:
    """Percentiles, probabilities and ES of one horizon's simulated log returns."""
    ordered = sorted(log_moves)
    n = len(ordered)
    simple = [math.exp(v) - 1 for v in ordered]
    tail = simple[: max(1, int(0.05 * n))]
    out = {f"p{p}": _r(price * math.exp(_quantile(ordered, p / 100)), 2) for p in PERCENTILES}
    out |= {
        "p_up": _r(sum(1 for v in simple if v > 0) / n),
        "p_drop_10": _r(sum(1 for v in simple if v < -0.10) / n),
        "p_gain_10": _r(sum(1 for v in simple if v > 0.10) / n),
        # Average loss on the worst 5% of paths, as a positive fraction (risk.py's sign).
        "expected_shortfall_95": _r(-_mean(tail)),
    }
    return out


def _normal_stats(price: float, sigma_daily: float, sessions: int) -> dict:
    """The same figures under a zero-drift normal log return with constant σ."""
    s = sigma_daily * math.sqrt(sessions)
    q05 = _N.inv_cdf(0.05) * s
    # E[e^X - 1 | X <= q] for X ~ N(0, s²): e^{s²/2} Φ((q - s²)/s) / 0.05 - 1.
    es = -(math.exp(s * s / 2) * _N.cdf((q05 - s * s) / s) / 0.05 - 1)
    out = {f"p{p}": _r(price * math.exp(_N.inv_cdf(p / 100) * s), 2) for p in PERCENTILES}
    out |= {
        "p_up": 0.5,
        "p_drop_10": _r(_N.cdf(math.log(0.9) / s)),
        "p_gain_10": _r(1 - _N.cdf(math.log(1.1) / s)),
        "expected_shortfall_95": _r(es),
    }
    return out


def simulate(closes: list[float], horizons: Sequence[int] = (5, 21, 63), paths: int = 2000,
             seed: int = 7) -> dict | None:
    """Filtered historical simulation of the price over each horizon.

    None below a year of returns (GARCH's own floor) or if the fit fails.
    `paths` is reduced so paths × max(horizon) stays under `MAX_DRAWS`; the
    number actually run is reported.
    """
    rets = log_returns(closes)
    horizons = sorted({int(h) for h in horizons if int(h) > 0})
    if not horizons or len(rets) < GARCH_MIN_OBSERVATIONS or closes[-1] <= 0:
        return None
    fit = fit_garch(rets)
    if fit is None:
        return None
    z = standardised_residuals(rets, fit.omega, fit.alpha, fit.beta)
    steps = horizons[-1]
    paths = max(100, min(int(paths), MAX_DRAWS // steps))
    price = float(closes[-1])

    rng = random.Random(seed)  # noqa: S311  # nosec B311 - reproducible simulation, not crypto
    draw = rng.random
    nz = len(z)
    omega, alpha, beta = fit.omega, fit.alpha, fit.beta
    by_step: list[list[float]] = [[0.0] * paths for _ in range(steps)]
    for p in range(paths):
        var, cum = fit.next_variance, 0.0
        for k in range(steps):
            shock = math.sqrt(var) * z[int(draw() * nz)]
            cum += shock
            by_step[k][p] = cum
            var = omega + alpha * shock * shock + beta * var

    sigma = _stdev(rets)
    out_h = []
    for h in horizons:
        fhs = _horizon_stats(price, by_step[h - 1])
        normal = _normal_stats(price, sigma, h)
        ratio = (fhs["expected_shortfall_95"] / normal["expected_shortfall_95"]
                 if normal["expected_shortfall_95"] else None)
        out_h.append({"sessions": h, "fhs": fhs, "normal": normal,
                      "tail_ratio": _r(ratio, 3)})

    fan_steps = list(range(1, steps + 1))
    if len(fan_steps) > FAN_MAX_POINTS:
        stride = math.ceil(len(fan_steps) / FAN_MAX_POINTS)
        fan_steps = fan_steps[stride - 1::stride]
        if fan_steps[-1] != steps:
            fan_steps[-1] = steps
    fan = []
    for k in fan_steps:
        ordered = sorted(by_step[k - 1])
        fan.append({"session": k} | {
            f"p{p}": _r(price * math.exp(_quantile(ordered, p / 100)), 2) for p in PERCENTILES})

    result = {
        "method": "filtered historical simulation (GARCH(1,1), bootstrapped residuals)",
        "last_price": price,
        "paths": paths,
        "seed": seed,
        "observations": len(rets),
        "garch": {
            "omega": fit.omega, "alpha": _r(fit.alpha), "beta": _r(fit.beta),
            "persistence": _r(fit.persistence),
            "current_vol_annual": _r(math.sqrt(fit.next_variance * TRADING_DAYS)),
            "long_run_vol_annual": _r(math.sqrt(fit.long_run_variance * TRADING_DAYS))
            if fit.persistence < 1 else None,
        },
        "normal_vol_annual": _r(sigma * math.sqrt(TRADING_DAYS)),
        "residual_excess_kurtosis": _r(excess_kurtosis(z), 3),
        "horizons": out_h,
        "fan": fan,
    }
    result["reading"] = _simulate_reading(result)
    return result


def _simulate_reading(r: dict) -> str:
    last = r["horizons"][-1]
    h, fhs, normal = last["sessions"], last["fhs"], last["normal"]
    g = r["garch"]
    text = (f"Over {h} sessions the simulated middle 90% runs from {fhs['p5']:.2f} to "
            f"{fhs['p95']:.2f}; the chance of a fall of more than 10% is "
            f"{fhs['p_drop_10'] * 100:.0f}% (a constant-volatility normal model says "
            f"{normal['p_drop_10'] * 100:.0f}%).")
    now, long_run = g["current_vol_annual"], g["long_run_vol_annual"]
    if long_run and abs(now / long_run - 1) < 0.05:
        text += (f" Volatility today ({now * 100:.0f}%) is close to its long-run level, so "
                 f"the cone widens at a steady rate.")
    elif long_run:
        above = now > long_run
        text += (f" Volatility today ({now * 100:.0f}%) is {'above' if above else 'below'} "
                 f"its long-run level ({long_run * 100:.0f}%), so the near-term cone starts "
                 f"{'wider' if above else 'narrower'} and reverts.")
    ratio = last["tail_ratio"]
    if ratio is not None:
        text += (f" The average loss in the worst 5% of paths is {ratio:.2f}× the normal "
                 f"model's — {'fatter' if ratio > 1.05 else 'no fatter'} tails than a "
                 f"bell curve.")
    return text


# ----------------------------------------------------------------------- shocks


def shocks(stock_closes: list[float], market_closes: list[float],
           sector_closes: list[float] | None = None,
           market_moves: Sequence[float] = (-0.20, -0.10, -0.05, 0.05, 0.10)) -> list[dict]:
    """What a given 21-session market move implies for this stock, two ways.

    **Model:** the market move (a simple return) in log terms, times the
    downside beta for a fall or the upside beta for a rise — a stock that falls
    harder than it rises should not have that averaged away. The band is ±1σ of
    everything the market does not explain (sector-beyond-market plus
    idiosyncratic), scaled by √21: a shock fixes the market, not the rest.

    **History:** the stock's median return over past overlapping 21-session
    windows where the market moved within ±2.5pp of that size. Overlapping
    windows share days, so `n` counts windows, not independent episodes —
    roughly n/21 of those. An empty `n` is the honest answer for a crash the
    sample never saw.

    Empty list below 120 aligned returns.
    """
    index, rets = aligned_returns(stock_closes, market_closes, sector_closes)
    if len(index) < MIN_RETURNS:
        return []
    y, m = rets[0], rets[1]
    asym = asymmetric_beta(y, m)
    fit = ols(y, [m])
    if fit is None:
        return []
    beta_all = fit["coef"][1]
    resid_daily = math.sqrt(sum(e * e for e in fit["resid"]) / fit["dof"])
    band = resid_daily * math.sqrt(SHOCK_HORIZON)

    sector_beta = None
    if sector_closes is not None:
        s_fit = ols(rets[2], [m])
        sector_beta = s_fit["coef"][1] if s_fit else None

    # Tail-aligned closes, for window returns that include days dropped above.
    n = min(len(stock_closes), len(market_closes))
    sc, mc = stock_closes[len(stock_closes) - n:], market_closes[len(market_closes) - n:]
    windows = [(sc[i] / sc[i - SHOCK_HORIZON] - 1, mc[i] / mc[i - SHOCK_HORIZON] - 1)
               for i in range(SHOCK_HORIZON, n)
               if sc[i - SHOCK_HORIZON] > 0 and mc[i - SHOCK_HORIZON] > 0]

    out = []
    for move in market_moves:
        if move <= -1:
            continue
        kind = "downside" if move < 0 else "upside"
        beta = asym[f"{kind}_beta"]
        if beta is None:
            beta, kind = beta_all, "overall"
        centre = beta * math.log1p(move)
        matched = sorted(s for s, mk in windows if abs(mk - move) <= ANALOG_TOLERANCE)
        median = _quantile(matched, 0.5) if matched else None
        expected = math.expm1(centre)
        item = {
            "market_move": move,
            "horizon": SHOCK_HORIZON,
            "beta_used": _r(beta),
            "beta_kind": kind,
            "expected": _r(expected),
            "low": _r(math.expm1(centre - band)),
            "high": _r(math.expm1(centre + band)),
            "sector_expected": _r(math.expm1(sector_beta * math.log1p(move)))
            if sector_beta is not None else None,
            "analog": {"median": _r(median), "n": len(matched)},
        }
        hist = (f"in {len(matched)} past windows like it the stock's median was "
                f"{median * 100:+.1f}%" if median is not None
                else "the sample has no market move of that size to compare against")
        item["reading"] = (
            f"If the market moves {move * 100:+.0f}% over a month, the {kind} beta of "
            f"{beta:.2f} implies {expected * 100:+.1f}% (±1σ band "
            f"{item['low'] * 100:+.1f}% to {item['high'] * 100:+.1f}%); {hist}.")
        out.append(item)
    return out
