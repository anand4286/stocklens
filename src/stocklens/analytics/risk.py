"""Risk and return statistics over daily closes.

Float maths, standard definitions, and `None` below a sample floor. Anything
annualised uses 252 sessions. Value at risk is historical (no normality
assumption), because daily equity returns are fat-tailed and a parametric VaR
would understate exactly the days it exists to describe.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

TRADING_DAYS = 252
#: RiskMetrics (J.P. Morgan 1996) daily decay.
EWMA_LAMBDA = 0.94


def log_returns(prices: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False) if a > 0 and b > 0]


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def annualised_vol(rets: Sequence[float]) -> float | None:
    return _stdev(rets) * math.sqrt(TRADING_DAYS) if len(rets) >= 20 else None


def ewma_vol(rets: Sequence[float], lam: float = EWMA_LAMBDA) -> float | None:
    if len(rets) < 20:
        return None
    var = sum(r * r for r in rets[:20]) / 20
    for r in rets[20:]:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var * TRADING_DAYS)


def max_drawdown(prices: Sequence[float]) -> float | None:
    if len(prices) < 2:
        return None
    peak, worst = prices[0], 0.0
    for p in prices:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1)
    return worst


def value_at_risk(rets: Sequence[float], level: float = 0.95) -> float | None:
    """Historical one-day VaR as a positive loss fraction."""
    if len(rets) < 100:
        return None
    ordered = sorted(rets)
    return -ordered[int((1 - level) * len(ordered))]


def expected_shortfall(rets: Sequence[float], level: float = 0.95) -> float | None:
    if len(rets) < 100:
        return None
    ordered = sorted(rets)
    tail = ordered[: max(1, int((1 - level) * len(ordered)))]
    return -_mean(tail)


def sharpe(rets: Sequence[float]) -> float | None:
    if len(rets) < 60:
        return None
    sd = _stdev(rets)
    return _mean(rets) / sd * math.sqrt(TRADING_DAYS) if sd else None


def sortino(rets: Sequence[float]) -> float | None:
    if len(rets) < 60:
        return None
    downside = [min(r, 0) for r in rets]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside))
    return _mean(rets) / dd * math.sqrt(TRADING_DAYS) if dd else None


def beta(asset: Sequence[float], market: Sequence[float]) -> float | None:
    n = min(len(asset), len(market))
    if n < 60:
        return None
    a, m = asset[-n:], market[-n:]
    ma, mm = _mean(a), _mean(m)
    cov = sum((x - ma) * (y - mm) for x, y in zip(a, m, strict=True)) / (n - 1)
    var = sum((y - mm) ** 2 for y in m) / (n - 1)
    return cov / var if var else None


def hurst(prices: Sequence[float]) -> float | None:
    """Rescaled-range Hurst exponent. ~0.5 random walk, >0.5 trending, <0.5 reverting.

    Plain R/S is biased upward in small samples; read 0.45-0.60 as
    indistinguishable from a random walk at these lengths.
    """
    rets = log_returns(prices)
    if len(rets) < 128:
        return None
    sizes, rs = [], []
    size = 8
    while size <= len(rets) // 2:
        chunks = [rets[i:i + size] for i in range(0, len(rets) - size + 1, size)]
        vals = []
        for c in chunks:
            m = _mean(c)
            dev, cum, lo, hi = 0.0, 0.0, 0.0, 0.0
            for x in c:
                cum += x - m
                lo, hi = min(lo, cum), max(hi, cum)
                dev += (x - m) ** 2
            sd = math.sqrt(dev / len(c))
            if sd > 0:
                vals.append((hi - lo) / sd)
        if vals:
            sizes.append(math.log(size))
            rs.append(math.log(_mean(vals)))
        size *= 2
    if len(sizes) < 3:
        return None
    mx, my = _mean(sizes), _mean(rs)
    num = sum((x - mx) * (y - my) for x, y in zip(sizes, rs, strict=True))
    den = sum((x - mx) ** 2 for x in sizes)
    return num / den if den else None


def variance_ratio(rets: Sequence[float], period: int = 5) -> float | None:
    """Lo & MacKinlay (1988). 1 = random walk; >1 momentum; <1 mean reversion."""
    if len(rets) < period * 20:
        return None
    one = _stdev(rets) ** 2
    agg = [sum(rets[i:i + period]) for i in range(len(rets) - period + 1)]
    return (_stdev(agg) ** 2) / (period * one) if one else None


def range_cone(price: float, vol_annual: float | None,
               horizons: Sequence[int] = (5, 21, 63)) -> list[dict]:
    """One-sigma lognormal price range per horizon.

    This is a statement about spread, not direction: the centre is today's
    price. It answers "how far could this plausibly move", which is the honest
    question when no model has earned the right to answer "which way".
    """
    if not vol_annual:
        return []
    out = []
    for h in horizons:
        sigma = vol_annual * math.sqrt(h / TRADING_DAYS)
        out.append({"sessions": h, "sigma_pct": round(sigma, 4),
                    "low": round(price * math.exp(-sigma), 2),
                    "high": round(price * math.exp(sigma), 2)})
    return out


def summary(prices: Sequence[float], benchmark_prices: Sequence[float] | None) -> dict:
    rets = log_returns(prices)
    bench = log_returns(benchmark_prices) if benchmark_prices else []
    vol = annualised_vol(rets[-252:])
    ewma = ewma_vol(rets)

    def r(x, n=4):
        return None if x is None else round(x, n)

    return {
        "volatility_annual": r(vol),
        "volatility_ewma": r(ewma),
        "max_drawdown": r(max_drawdown(prices[-252:])),
        "var_95_1d": r(value_at_risk(rets[-500:])),
        "es_95_1d": r(expected_shortfall(rets[-500:])),
        "sharpe": r(sharpe(rets[-252:]), 2),
        "sortino": r(sortino(rets[-252:]), 2),
        "beta": r(beta(rets[-252:], bench[-252:]), 2) if bench else None,
        "hurst": r(hurst(prices), 3),
        "variance_ratio_5d": r(variance_ratio(rets[-252:]), 3),
        "range_cone": range_cone(prices[-1], ewma or vol),
        "observations": len(rets),
    }
