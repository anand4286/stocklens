"""Market regime classification, and what it does to the composite's weights.

Why regime matters more than any single indicator
-------------------------------------------------
The same signal has the opposite meaning in different regimes. An oversold RSI
is a reasonable contrarian read in a mean-reverting market and a poor one in a
trending market, where weakness tends to continue and "oversold" is a warning
rather than an invitation. Twelve-one momentum is the mirror image. A composite
that weights its families identically everywhere is implicitly betting the
regime never changes.

So the regime is estimated first, and it tilts which evidence the composite
leans on.

Estimation
----------
Two independent measures, which must **agree** before a regime is asserted:

- the small-sample corrected **Hurst exponent** (`quant.volatility`), and
- the Lo–MacKinlay **variance ratio** VR(5): whether five-day variance is five
  times one-day variance, as independence requires.

Both are noisy on a few hundred daily bars, so each is read through a noise
band of two of its own standard errors. Inside the band, it votes "no
evidence". Only when both vote the same direction is a regime named; anything
else — one silent, or the two pointing opposite ways — is `random`. Two weak
measures that disagree are not evidence, and a regime call drawn from noise
would re-weight the composite on noise.

Volatility regime is separate and simpler: current 21-session realised vol
as a percentile of its own rolling history. Compared against itself, not a
fixed threshold, because 40% annualised is calm for a biotech and a crisis for
a utility.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from stocklens.analytics.risk import log_returns
from stocklens.quant.volatility import hurst_corrected, percentile_of, rolling_vol

TRENDING = "trending"
MEAN_REVERTING = "mean_reverting"
RANDOM = "random"

#: Centre of the corrected Hurst estimator's *null* distribution, measured
#: rather than assumed: 300 simulated Gaussian random walks give a mean of
#: 0.473 at 250 bars, 0.473 at 400, 0.478 at 750 and 0.483 at 1250. Theory says
#: 0.5; the residual bias is small in absolute terms and decisive in practice,
#: because the decision is a comparison against a threshold.
HURST_NULL_MEAN = 0.475

#: The same simulation's standard deviation is close to ``1 / sqrt(n)`` (0.062
#: at 250 bars, 0.045 at 400, 0.035 at 1250). Floored so a very long history
#: cannot shrink the band to nothing on what is still one estimator's noise.
_HURST_SD_FLOOR = 0.03

#: Two standard errors: a regime is asserted on roughly one noise series in
#: twenty per measure, and far less often when both must agree.
NOISE_BAND_SE = 2.0

#: Variance-ratio horizon.
VR_PERIOD = 5

VOL_WINDOW = 21
VOL_MIN_HISTORY = 60
VOL_LOW_PCT = 0.20
VOL_HIGH_PCT = 0.80

#: Composite families. "technical" is the short-horizon, RSI-led
#: mean-reversion family; "momentum" is 12-1 and "relative_strength" is
#: performance against the benchmark — both continuation bets. Fundamental,
#: valuation and sentiment do not depend on price behaviour and are left alone.
FAMILIES = ("technical", "momentum", "relative_strength", "fundamental", "valuation", "sentiment")

#: Multipliers on each family's base weight, per regime. **Picked, not
#: fitted**: they encode the direction of the argument above, and their size is
#: a judgement deliberately kept inside [0.5, 1.5] so that no regime call — which
#: is itself an estimate — can more than halve or one-and-a-half a family's
#: say. There is no backtest behind these numbers, and none should be implied.
REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
    TRENDING: {"technical": 0.65, "momentum": 1.35, "relative_strength": 1.25},
    MEAN_REVERTING: {"technical": 1.35, "momentum": 0.70, "relative_strength": 0.80},
    RANDOM: {},
}
MULTIPLIER_BOUNDS = (0.5, 1.5)


def variance_ratio(returns: Sequence[float], period: int = VR_PERIOD) -> float | None:
    """Lo–MacKinlay (1988) overlapping variance ratio, bias-corrected.

    ``VR(q) = σ²_c / σ²_a``: the variance of overlapping q-day sums (per day)
    over the one-day variance. Under independence it is 1; above 1 means
    positive autocorrelation (trending), below 1 negative (mean-reverting).
    Overlapping sums use every q-day window rather than every q-th, which is
    what makes the asymptotic variance below small enough to be useful.
    """
    n = len(returns)
    if n < period * 20:
        return None
    mu = sum(returns) / n
    var_a = sum((r - mu) ** 2 for r in returns) / (n - 1)
    if var_a == 0:
        return None
    m = period * (n - period + 1) * (1 - period / n)
    window = sum(returns[:period])
    total = (window - period * mu) ** 2
    for i in range(period, n):
        window += returns[i] - returns[i - period]
        total += (window - period * mu) ** 2
    return (total / m) / var_a


def variance_ratio_se(n: int, period: int = VR_PERIOD) -> float:
    """Asymptotic standard error of VR(q) under homoskedastic independence.

    ``Var = 2(2q − 1)(q − 1) / (3qn)``. For q = 5 that is about 0.14 on 250
    sessions and 0.06 on 1250 — which is why the band is computed from the
    sample size rather than fixed: a fixed 0.15 is less than one standard error
    on a year of data and more than two on five.
    """
    return math.sqrt(2 * (2 * period - 1) * (period - 1) / (3 * period * n))


def hurst_null_sd(n: int) -> float:
    return max(_HURST_SD_FLOOR, 1.0 / math.sqrt(n))


def _vote(value: float | None, centre: float, band: float) -> int:
    if value is None:
        return 0
    if value > centre + band:
        return 1
    if value < centre - band:
        return -1
    return 0


def volatility_regime(returns: Sequence[float]) -> tuple[str, float | None]:
    """(state, percentile) of current 21-session vol against its own rolling history."""
    history = rolling_vol(returns, VOL_WINDOW)
    if len(history) < VOL_MIN_HISTORY:
        return "unknown", None
    pct = percentile_of(history[-1], history)
    if pct >= VOL_HIGH_PCT:
        return "high", pct
    if pct <= VOL_LOW_PCT:
        return "low", pct
    return "normal", pct


def classify(closes: list[float]) -> dict:
    """Trend and volatility regime from oldest-first closes, JSON-ready.

    ``agree`` is true when the two trend measures cast the same vote — both
    trending, both mean-reverting, or both inside their noise bands. It is false
    when one sees structure the other does not, and in that case the trend is
    reported as `random` whatever the stronger measure says.
    """
    rets = log_returns(closes)
    hurst = hurst_corrected(closes)
    ratio = variance_ratio(rets)
    vol_state, vol_pct = volatility_regime(rets)

    n = len(rets)
    h_band = NOISE_BAND_SE * hurst_null_sd(n) if n else 0.0
    v_band = NOISE_BAND_SE * variance_ratio_se(n) if n else 0.0
    h_vote = _vote(hurst, HURST_NULL_MEAN, h_band)
    v_vote = _vote(ratio, 1.0, v_band)
    agree = hurst is not None and ratio is not None and h_vote == v_vote

    if hurst is None or ratio is None:
        trend = RANDOM
        reason = "not enough history to estimate both Hurst and the variance ratio"
    elif agree and h_vote == 1:
        trend = TRENDING
        reason = (
            f"Hurst {hurst:.2f} above its noise band ({HURST_NULL_MEAN + h_band:.2f}) and "
            f"variance ratio {ratio:.2f} above {1 + v_band:.2f}: moves have tended to continue"
        )
    elif agree and h_vote == -1:
        trend = MEAN_REVERTING
        reason = (
            f"Hurst {hurst:.2f} below its noise band ({HURST_NULL_MEAN - h_band:.2f}) and "
            f"variance ratio {ratio:.2f} below {1 - v_band:.2f}: moves have tended to reverse"
        )
    elif agree:
        trend = RANDOM
        reason = (
            f"Hurst {hurst:.2f} and variance ratio {ratio:.2f} both sit inside their noise "
            "bands: no exploitable memory in the price path"
        )
    else:
        trend = RANDOM
        reason = (
            f"Hurst {hurst:.2f} and variance ratio {ratio:.2f} disagree, and two weak "
            "measures pointing different ways is not evidence of a regime"
        )

    return {
        "trend": trend,
        "volatility": vol_state,
        "hurst": None if hurst is None else round(hurst, 4),
        "variance_ratio": None if ratio is None else round(ratio, 4),
        "vol_percentile": None if vol_pct is None else round(vol_pct, 4),
        "agree": agree,
        "reason": reason,
        "multipliers": multipliers_for(trend),
    }


def multipliers_for(trend: str) -> dict[str, float]:
    """Bounded multiplier for every family under a trend regime (1.0 if untouched)."""
    lo, hi = MULTIPLIER_BOUNDS
    tilts = REGIME_MULTIPLIERS.get(trend, {})
    return {f: min(hi, max(lo, tilts.get(f, 1.0))) for f in FAMILIES}


def tilt_weights(weights: dict[str, float], regime: dict) -> dict[str, float]:
    """Re-weight composite families for the regime, preserving the total.

    Each weight is multiplied by its family's multiplier (unknown families get
    1.0), then all are rescaled so they sum to what the input summed to. That
    changes the *balance* of evidence and never the amount of it, so a caller
    whose weights sum to 1 still gets weights that sum to 1. A `random` regime
    returns the weights unchanged.
    """
    multipliers = regime.get("multipliers") or multipliers_for(regime.get("trend", RANDOM))
    lo, hi = MULTIPLIER_BOUNDS
    total = sum(weights.values())
    adjusted = {k: w * min(hi, max(lo, multipliers.get(k, 1.0))) for k, w in weights.items()}
    new_total = sum(adjusted.values())
    if new_total <= 0 or total <= 0:
        return dict(weights)
    scale = total / new_total
    return {k: v * scale for k, v in adjusted.items()}
