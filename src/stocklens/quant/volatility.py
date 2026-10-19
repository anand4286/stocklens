"""Volatility models and distributional diagnostics for one price series.

Why more than one volatility number
-----------------------------------
`analytics.risk` reports a close-to-close standard deviation and an EWMA. Both
answer "how much has this moved", and neither answers the questions a reader
actually has next:

- **Where is it going?** Volatility clusters: a violent day is followed by more
  violent days, and calm by calm. A constant-variance model cannot express that,
  so it under-forecasts risk exactly when risk is highest. GARCH(1,1) models
  tomorrow's variance as a weighted sum of a long-run level, today's squared
  shock and today's variance, and so gives a *term structure*: what the next
  week, month and half-year should average, reverting towards the long run at a
  speed the data itself estimates.
- **Is the close-to-close number efficient?** It uses one price per day and
  throws away the high and the low. Parkinson (1980) and Garman–Klass (1980)
  use the day's range, and are several times more efficient for the same
  sample — which is why they are reported side by side with it rather than
  instead of it: they ignore the overnight gap, so a stock that gaps on news
  reads *lower* on a range estimator than on closes, and that difference is
  itself information.
- **Is today's level unusual?** A volatility cone places current realised vol
  at several horizons against its own history. 40% annualised is calm for a
  biotech and a crisis for a utility; a percentile says which this is.
- **Is the normal model even close?** Skewness, excess kurtosis and the
  Jarque–Bera test say how far the return distribution sits from the Gaussian
  that every parametric figure here silently leans on.

Numeric policy
--------------
Float maths, stdlib only, 252 sessions a year. Every estimator here is a random
variable; each documents the sample it needs and returns `None` below it rather
than a confident-looking number computed from too little data. Callers must
treat `None` as "unknown", never as zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from stocklens.analytics.risk import log_returns

TRADING_DAYS = 252

#: A year of sessions. GARCH's persistence is identified from how shocks decay,
#: and on fewer than one year of data the likelihood surface along alpha + beta
#: is close to flat: the fit returns *a* number, not a measurement.
GARCH_MIN_OBSERVATIONS = 250

#: Horizons for the variance term structure: a week, a month, a quarter, half a year.
FORECAST_HORIZONS = (5, 21, 63, 126)

#: Range estimators are computed over the last quarter of sessions.
RANGE_WINDOW = 63
RANGE_MIN_SESSIONS = 20

#: Cone windows (two weeks, a month, a quarter) and the history each needs: at
#: least this many rolling values before a percentile means anything.
CONE_WINDOWS = (10, 21, 63)
CONE_MIN_POINTS = 60

#: 5% critical value of chi-squared with two degrees of freedom, the null
#: distribution of Jarque–Bera. Closed form: -2 ln(0.05).
_JB_CRITICAL_95 = -2.0 * math.log(0.05)


# --------------------------------------------------------------------------- moments


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _pstdev(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _stdev(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def skewness(values: Sequence[float]) -> float | None:
    """Third standardised moment. Negative means a long left tail.

    Equities usually show negative skew: crashes are faster than rallies. A
    return stream that is negatively skewed wins often and loses big, which a
    hit rate alone will not reveal.
    """
    n = len(values)
    if n < 30:
        return None
    sigma = _pstdev(values)
    if sigma == 0:
        return None
    mu = _mean(values)
    return sum(((v - mu) / sigma) ** 3 for v in values) / n


def excess_kurtosis(values: Sequence[float]) -> float | None:
    """Fourth standardised moment minus 3. Zero is Gaussian; positive is fat-tailed.

    Daily equity returns typically show excess kurtosis of 3 or more, which is
    why any risk number derived from a normal assumption understates the tail.
    """
    n = len(values)
    if n < 30:
        return None
    sigma = _pstdev(values)
    if sigma == 0:
        return None
    mu = _mean(values)
    return sum(((v - mu) / sigma) ** 4 for v in values) / n - 3.0


def jarque_bera(values: Sequence[float]) -> float | None:
    """Jarque–Bera statistic: ``n/6 · (S² + K²/4)``, with K the excess kurtosis.

    Under normality it is chi-squared with two degrees of freedom, so it rejects
    at 5% above about 5.99. On daily equity returns it almost always rejects —
    and that is the point of reporting it: it is the standing reminder that the
    Gaussian is a convenience, not a description.
    """
    s, k = skewness(values), excess_kurtosis(values)
    if s is None or k is None:
        return None
    return len(values) / 6.0 * (s * s + k * k / 4.0)


def autocorrelation(returns: Sequence[float], lag: int = 1) -> float | None:
    """Serial correlation of returns at a lag.

    Annualising by sqrt(252) assumes this is zero. A materially positive value
    means the close-to-close vol understates multi-day risk; negative means it
    overstates it.
    """
    n = len(returns)
    if n < lag + 30:
        return None
    mu = _mean(returns)
    den = sum((r - mu) ** 2 for r in returns)
    if den == 0:
        return None
    return sum((returns[i] - mu) * (returns[i - lag] - mu) for i in range(lag, n)) / den


def ulcer_index(prices: Sequence[float]) -> float | None:
    """Root-mean-square percentage drawdown.

    Maximum drawdown reports a single worst moment. The Ulcer Index (Martin,
    1987) measures how deep *and how long* the underwater periods were, which is
    closer to what an investor actually lives through.
    """
    if len(prices) < 20:
        return None
    peak = prices[0]
    squares = []
    for p in prices:
        peak = max(peak, p)
        if peak > 0:
            squares.append((100.0 * (p - peak) / peak) ** 2)
    return math.sqrt(_mean(squares)) if squares else None


# ----------------------------------------------------------------------- GARCH(1,1)


@dataclass(frozen=True, slots=True)
class GarchFit:
    """A fitted GARCH(1,1): ``σ²[t+1] = ω + α r²[t] + β σ²[t]``, daily units."""

    omega: float
    alpha: float
    beta: float
    log_likelihood: float
    observations: int
    #: One-step-ahead conditional variance (daily). Kept un-annualised because
    #: the forecast recursion iterates on it.
    next_variance: float

    @property
    def persistence(self) -> float:
        """alpha + beta. Approaching 1 means shocks decay very slowly."""
        return self.alpha + self.beta

    @property
    def long_run_variance(self) -> float:
        return self.omega / (1.0 - self.persistence)

    @property
    def half_life(self) -> float | None:
        """Sessions for a variance shock to decay by half: ``ln(0.5) / ln(α + β)``."""
        p = self.persistence
        if not 0 < p < 1:
            return None
        return math.log(0.5) / math.log(p)

    def forecast(self, sessions: int) -> float | None:
        """Average annualised volatility expected over the next `sessions`.

        Not tomorrow's volatility: the *mean* over the horizon. After a shock the
        two differ sharply, because GARCH's own mean reversion pulls the path back
        towards the long run. With ``p = α + β`` and long-run variance ``s²``:

            var[t+k] = s² + p^(k-1) (var[t+1] − s²)
            mean over k = 1..h = s² + (var[t+1] − s²) (1 − p^h) / (h (1 − p))
        """
        if sessions <= 0 or self.next_variance <= 0:
            return None
        p = self.persistence
        s2 = self.long_run_variance
        if sessions == 1:
            mean_var = self.next_variance
        else:
            decay = (1.0 - p**sessions) / (sessions * (1.0 - p))
            mean_var = s2 + (self.next_variance - s2) * decay
        return math.sqrt(mean_var * TRADING_DAYS) if mean_var > 0 else None


def _garch_loglik(returns: Sequence[float], omega: float, alpha: float, beta: float) -> float:
    """Gaussian log-likelihood, recursion seeded at the sample variance."""
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
        return -math.inf
    var = sum(r * r for r in returns) / len(returns)
    total = 0.0
    log2pi = math.log(2 * math.pi)
    for r in returns:
        if var <= 1e-14:
            return -math.inf
        total -= 0.5 * (log2pi + math.log(var) + r * r / var)
        var = omega + alpha * r * r + beta * var
    return total


def fit_garch(returns: Sequence[float]) -> GarchFit | None:
    """Fit GARCH(1,1) by maximum likelihood, with a derivative-free optimiser.

    Returns are demeaned first: the daily mean is two orders of magnitude below
    the daily standard deviation, and leaving it in biases alpha upward.

    The search runs in two stages because it cannot fail to
    converge and has no dependency to import:

    1. A coarse grid over (alpha, beta) with omega *variance-targeted* — set so
       the long-run variance equals the sample variance. That reduces the
       search to two dimensions and lands near the optimum.
    2. A compass (pattern) search over all three parameters from there, halving
       the step whenever no neighbour improves, until steps are below 1e-4.

    Stationarity, ``α + β < 1``, is enforced as a hard constraint: an
    integrated model has no long-run variance and its forecast grows without
    bound, which is a statement about the model, not about the stock.
    """
    if len(returns) < GARCH_MIN_OBSERVATIONS:
        return None
    mu = _mean(returns)
    rets = [r - mu for r in returns]
    sample_var = sum(r * r for r in rets) / len(rets)
    if sample_var <= 0:
        return None

    best: tuple[float, float, float, float] | None = None
    for a_i in range(1, 31, 2):
        alpha = a_i / 100
        for b_i in range(50, 99, 2):
            beta = b_i / 100
            if alpha + beta >= 0.995:
                continue
            omega = sample_var * (1 - alpha - beta)
            score = _garch_loglik(rets, omega, alpha, beta)
            if best is None or score > best[0]:
                best = (score, omega, alpha, beta)
    if best is None or not math.isfinite(best[0]):
        return None

    # Compass search. omega is searched in log space, since it is a small
    # positive number whose scale is set by the data, not by the unit interval.
    score, omega, alpha, beta = best
    params = [math.log(omega), alpha, beta]
    steps = [0.5, 0.02, 0.02]
    for _ in range(400):
        if max(steps[1], steps[2]) < 1e-4 and steps[0] < 1e-3:
            break
        improved = False
        for i in range(3):
            for sign in (1.0, -1.0):
                trial = list(params)
                trial[i] += sign * steps[i]
                s = _garch_loglik(rets, math.exp(trial[0]), trial[1], trial[2])
                if s > score:
                    score, params, improved = s, trial, True
        if not improved:
            steps = [s / 2 for s in steps]
    omega, alpha, beta = math.exp(params[0]), params[1], params[2]

    var = sample_var
    for r in rets:
        var = omega + alpha * r * r + beta * var
    return GarchFit(
        omega=omega,
        alpha=alpha,
        beta=beta,
        log_likelihood=score,
        observations=len(rets),
        next_variance=var,
    )


# ------------------------------------------------------------------ range estimators


def close_to_close_vol(closes: Sequence[float]) -> float | None:
    rets = log_returns(closes)
    if len(rets) < RANGE_MIN_SESSIONS:
        return None
    return _stdev(rets) * math.sqrt(TRADING_DAYS)


def parkinson_vol(highs: Sequence[float], lows: Sequence[float]) -> float | None:
    """Parkinson (1980): ``σ² = mean(ln(H/L)²) / (4 ln 2)``.

    About five times as efficient as close-to-close for a driftless diffusion,
    because the range carries information the two endpoints discard. It assumes
    continuous trading, so it misses the overnight gap and reads low on stocks
    that move mostly between sessions.
    """
    terms = [math.log(h / lo) ** 2 for h, lo in zip(highs, lows, strict=False) if h > 0 and lo > 0]
    if len(terms) < RANGE_MIN_SESSIONS:
        return None
    return math.sqrt(_mean(terms) / (4.0 * math.log(2.0)) * TRADING_DAYS)


def garman_klass_vol(
    opens: Sequence[float], highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> float | None:
    """Garman–Klass (1980): ``σ² = mean(½ ln(H/L)² − (2 ln 2 − 1) ln(C/O)²)``.

    Adds the open-to-close move to Parkinson's range, for roughly eight times
    close-to-close efficiency. Like Parkinson it ignores the overnight gap.
    """
    k = 2.0 * math.log(2.0) - 1.0
    terms = []
    for o, h, lo, c in zip(opens, highs, lows, closes, strict=False):
        if min(o, h, lo, c) <= 0:
            continue
        terms.append(0.5 * math.log(h / lo) ** 2 - k * math.log(c / o) ** 2)
    if len(terms) < RANGE_MIN_SESSIONS:
        return None
    var = _mean(terms)
    return math.sqrt(var * TRADING_DAYS) if var > 0 else None


# ------------------------------------------------------------------------ vol cone


def _quantile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile of an already-sorted sequence."""
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def percentile_of(value: float, history: Sequence[float]) -> float:
    """Fraction of `history` strictly below `value`, ties counted half (0..1)."""
    below = sum(1 for h in history if h < value)
    ties = sum(1 for h in history if h == value)
    return (below + 0.5 * ties) / len(history)


def rolling_vol(returns: Sequence[float], window: int) -> list[float]:
    """Annualised realised vol over every overlapping `window` of returns."""
    if len(returns) < window:
        return []
    # Running sums keep this O(n) rather than O(n·window).
    s = sum(returns[:window])
    s2 = sum(r * r for r in returns[:window])
    out = []
    for end in range(window, len(returns) + 1):
        if end > window:
            old, new = returns[end - window - 1], returns[end - 1]
            s += new - old
            s2 += new * new - old * old
        var = max((s2 - s * s / window) / (window - 1), 0.0)
        out.append(math.sqrt(var * TRADING_DAYS))
    return out


def vol_cone(returns: Sequence[float], windows: Sequence[int] = CONE_WINDOWS) -> list[dict]:
    """Realised-vol cone: 10th/50th/90th percentile of rolling vol per window.

    Overlapping windows are used, so neighbouring values are highly correlated
    and the effective number of independent observations is about
    ``len(history) / window``. The floor is on the raw count; the 63-session
    percentile on two years is therefore coarse, and the cone says so by being
    wide rather than by pretending otherwise.
    """
    out = []
    for w in windows:
        hist = rolling_vol(returns, w)
        if len(hist) < CONE_MIN_POINTS:
            out.append(
                {"window": w} | dict.fromkeys(("p10", "p50", "p90", "current", "percentile"))
            )
            continue
        ordered = sorted(hist)
        current = hist[-1]
        out.append(
            {
                "window": w,
                "p10": _quantile(ordered, 0.10),
                "p50": _quantile(ordered, 0.50),
                "p90": _quantile(ordered, 0.90),
                "current": current,
                "percentile": percentile_of(current, hist),
            }
        )
    return out


# -------------------------------------------------------------------------- Hurst


def _expected_rescaled_range(n: int) -> float:
    """Expected R/S of an *independent* series of length n (Anis–Lloyd, Peters).

    The R/S statistic is biased upward on finite samples: a true random walk
    does not produce H = 0.5 at these lengths, it produces something nearer
    0.57. Comparing a raw estimate against the theoretical 0.5 therefore reads
    ordinary noise as a trend. Anis and Lloyd (1976) give the expected value
    under independence; Peters (1994) adds the ``(n − ½)/n`` factor that fixes
    its remaining bias at small n. The corrected Hurst is measured against
    this, not against 0.5.
    """
    if n < 2:
        return 0.0
    tail = sum(math.sqrt((n - i) / i) for i in range(1, n))
    if n <= 340:
        front = math.gamma((n - 1) / 2) / (math.sqrt(math.pi) * math.gamma(n / 2))
    else:
        # Stirling form: gamma overflows well before this.
        front = 1.0 / math.sqrt(n * math.pi / 2)
    return (n - 0.5) / n * front * tail


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    mx, my = _mean(xs), _mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den


def hurst_corrected(prices: Sequence[float]) -> float | None:
    """Small-sample corrected rescaled-range Hurst exponent.

    - **H < 0.5** mean-reverting: moves tend to be given back.
    - **H ≈ 0.5** a random walk: no exploitable memory.
    - **H > 0.5** trending (persistent): moves tend to continue.

    The raw R/S slope is computed over dyadic window sizes, then the slope an
    independent series of the *same* window sizes would produce is subtracted
    and 0.5 added back. On a few hundred bars the result is still noisy — its
    standard error on 400 sessions is roughly 0.05 — and `quant.regime` reads it
    through a noise band rather than at face value.
    """
    if len(prices) < 100:
        return None
    series = log_returns(prices)
    if len(series) < 64:
        return None
    scales = []
    size = 8
    while size <= len(series) // 2:
        scales.append(size)
        size *= 2
    if len(scales) < 3:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for scale in scales:
        ratios = []
        for start in range(0, len(series) - scale + 1, scale):
            window = series[start : start + scale]
            mu = _mean(window)
            running, cum = 0.0, []
            for w in window:
                running += w - mu
                cum.append(running)
            spread = max(cum) - min(cum)
            sigma = _pstdev(window)
            if sigma > 0 and spread > 0:
                ratios.append(spread / sigma)
        if ratios:
            xs.append(math.log(scale))
            ys.append(math.log(_mean(ratios)))
    if len(xs) < 3:
        return None

    observed = _slope(xs, ys)
    if observed is None:
        return None
    expected_ys = [math.log(_expected_rescaled_range(round(math.exp(x)))) for x in xs]
    expected = _slope(xs, expected_ys)
    return observed if expected is None else observed - expected + 0.5


# ------------------------------------------------------------------------- summary


def _r(x: float | None, places: int = 6) -> float | None:
    return None if x is None else round(x, places)


def summary(bars_ohlc: dict[str, list[float]]) -> dict:
    """Everything above, JSON-ready, from oldest-first OHLC lists of equal length."""
    opens = list(bars_ohlc.get("open") or [])
    highs = list(bars_ohlc.get("high") or [])
    lows = list(bars_ohlc.get("low") or [])
    closes = list(bars_ohlc.get("close") or [])
    rets = log_returns(closes)

    fit = fit_garch(rets)
    garch = None
    forecast: list[dict] = []
    if fit is not None:
        garch = {
            "omega": fit.omega,
            "alpha": _r(fit.alpha),
            "beta": _r(fit.beta),
            "persistence": _r(fit.persistence),
            "half_life_sessions": _r(fit.half_life, 2),
            "long_run_vol_annual": _r(math.sqrt(fit.long_run_variance * TRADING_DAYS)),
            "current_vol_annual": _r(math.sqrt(fit.next_variance * TRADING_DAYS)),
            "log_likelihood": _r(fit.log_likelihood, 3),
            "observations": fit.observations,
        }
        for h in FORECAST_HORIZONS:
            v = fit.forecast(h)
            if v is not None:
                forecast.append({"sessions": h, "vol_annual": round(v, 6)})

    tail = RANGE_WINDOW
    range_estimators = {
        # One extra close so the window holds `tail` returns.
        "close_to_close": _r(close_to_close_vol(closes[-(tail + 1) :])),
        "parkinson": _r(parkinson_vol(highs[-tail:], lows[-tail:])),
        "garman_klass": _r(
            garman_klass_vol(opens[-tail:], highs[-tail:], lows[-tail:], closes[-tail:])
        ),
    }

    cone = [
        {k: (_r(v) if isinstance(v, float) else v) for k, v in row.items()}
        for row in vol_cone(rets)
    ]

    jb = jarque_bera(rets)
    distribution = {
        "skewness": _r(skewness(rets), 4),
        "excess_kurtosis": _r(excess_kurtosis(rets), 4),
        "jarque_bera": _r(jb, 3),
        "normal_rejected": None if jb is None else jb > _JB_CRITICAL_95,
        "autocorr_lag1": _r(autocorrelation(rets, 1), 4),
        "ulcer_index": _r(ulcer_index(closes), 4),
    }

    return {
        "garch": garch,
        "forecast": forecast,
        "range_estimators": range_estimators,
        "cone": cone,
        "distribution": distribution,
        "hurst_corrected": _r(hurst_corrected(closes), 4),
    }
