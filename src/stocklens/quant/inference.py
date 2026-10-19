"""What a small sample can and cannot support.

The question this answers
-------------------------
A signal was right 60% of the time over thirty observations. A strategy shows
an annualised Sharpe of 1.2 over two years. Do either of those mean anything?

The usual answer is a normal-approximation standard error, which is the
standard move and is wrong at small samples. The central limit theorem is
asymptotic: it says the sampling distribution *approaches* normality, not that
it is normal at n = 30. A symmetric ±1.96σ interval around 60% on thirty
observations claims a precision the sample does not have, and claims it in a
way that looks rigorous.

Proportions
-----------
**Hoeffding (1963)** holds for every n, needs only independent observations
bounded in [0, 1], and makes no distributional claim. It is the loosest of the
intervals here, deliberately: the looseness is the honest part.
**Bernstein** is tighter when the variance is small — a hit rate near 0 or 1.
**Wilson (1927)** is the score interval: still asymptotic, but it inverts the
test rather than plugging in the estimate, so it never leaves [0, 1] and keeps
close to nominal coverage at small n where the Wald interval collapses.
**Normal (Wald)** is included for contrast, not for use.

Sharpe ratios
-------------
A Sharpe ratio is an estimate with a standard error, and that error grows with
negative skew and fat tails (Lo 2002; Mertens 2002). The **Probabilistic Sharpe
Ratio** (Bailey & López de Prado 2012) is the probability that the true Sharpe
exceeds a benchmark given the sample's length, skew and kurtosis. The
**Deflated Sharpe Ratio** (2014) raises that benchmark to the Sharpe the best
of N *unskilled* trials would be expected to show — because the highest of
many backtests is high partly by selection, and quoting it as if one test had
been run is the most common way a backtest lies. **Minimum Track Record
Length** inverts PSR: how many sessions this Sharpe would need before it could
be told apart from zero.

Multiple testing
----------------
Testing eleven candidate effects at |t| > 2 and keeping the ones that cleared
is a search, and the threshold that governs a search is not the one that
governs a single hypothesis. `bonferroni_threshold` and
`expected_false_survivors` put a number on that.

What none of this does
----------------------
None of these make a small sample informative. They make its
uninformativeness explicit and quantified.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

TRADING_DAYS = 252

#: Below this many daily returns no Sharpe inference is attempted. Sixty
#: sessions is a quarter; even there the standard error of an annualised
#: Sharpe is about two.
SHARPE_MIN_OBSERVATIONS = 60

#: Euler–Mascheroni constant, for the expected maximum of N Gaussian draws.
EULER_GAMMA = 0.5772156649015329

_N = NormalDist()


def _z(confidence: float) -> float:
    if not 0 < confidence < 1:
        raise ValueError("confidence must lie strictly between 0 and 1")
    return _N.inv_cdf(1.0 - (1.0 - confidence) / 2.0)


@dataclass(frozen=True, slots=True)
class Bound:
    """A confidence interval for a proportion, with the assumption it rests on named."""

    method: str
    point: float
    low: float
    high: float
    confidence: float
    samples: int
    assumption: str

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def excludes_coin_flip(self) -> bool:
        """Whether the interval excludes 0.5 — a directional edge on this evidence."""
        return self.low > 0.5 or self.high < 0.5

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "point": round(self.point, 6),
            "low": round(self.low, 6),
            "high": round(self.high, 6),
            "width": round(self.width, 6),
            "confidence": self.confidence,
            "samples": self.samples,
            "excludes_coin_flip": self.excludes_coin_flip,
            "assumption": self.assumption,
        }


def _clip(point: float, err: float) -> tuple[float, float]:
    return max(0.0, point - err), min(1.0, point + err)


def hoeffding_interval(successes: int, n: int, confidence: float = 0.95) -> Bound | None:
    """Distribution-free interval for a bounded mean.

    ``P(|p̂ − p| ≥ ε) ≤ 2 exp(−2nε²)``, inverted for ε. Holds for every n. At
    n = 30 and 95% it spans about ±25 points, which is the arithmetic stating
    plainly that thirty observations cannot tell a good signal from a coin.
    """
    if n <= 0 or not 0 <= successes <= n:
        return None
    point = successes / n
    eps = math.sqrt(math.log(2.0 / (1.0 - confidence)) / (2.0 * n))
    low, high = _clip(point, eps)
    return Bound(
        "Hoeffding",
        point,
        low,
        high,
        confidence,
        n,
        "independent observations bounded in [0, 1]; no distributional claim",
    )


def bernstein_interval(successes: int, n: int, confidence: float = 0.95) -> Bound | None:
    """Variance-aware interval: ``ε = √(2σ² log(2/δ)/n) + 2 log(2/δ)/(3n)``.

    Near 50% it barely improves on Hoeffding, because that is where a bounded
    variable's variance is largest; near 0 or 1 it is materially tighter.
    """
    if n <= 0 or not 0 <= successes <= n:
        return None
    point = successes / n
    log_term = math.log(2.0 / (1.0 - confidence))
    eps = math.sqrt(2.0 * point * (1 - point) * log_term / n) + 2.0 * log_term / (3.0 * n)
    low, high = _clip(point, eps)
    return Bound(
        "Bernstein",
        point,
        low,
        high,
        confidence,
        n,
        "independent bounded observations; uses the sample variance",
    )


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> Bound | None:
    """Wilson score interval.

    ``(p̂ + z²/2n ± z √(p̂(1−p̂)/n + z²/4n²)) / (1 + z²/n)``. It is centred not
    on p̂ but pulled towards ½, which is why it neither collapses to zero width
    at 0/n nor spills outside [0, 1] — the two failures of the Wald interval.
    """
    if n <= 0 or not 0 <= successes <= n:
        return None
    point = successes / n
    z = _z(confidence)
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (point + z2 / (2 * n)) / denom
    half = z / denom * math.sqrt(point * (1 - point) / n + z2 / (4 * n * n))
    return Bound(
        "Wilson score",
        point,
        max(0.0, centre - half),
        min(1.0, centre + half),
        confidence,
        n,
        "independent observations; asymptotic, but near nominal coverage at small n",
    )


def normal_interval(successes: int, n: int, confidence: float = 0.95) -> Bound | None:
    """The Wald interval, included for contrast rather than for use."""
    if n <= 0 or not 0 <= successes <= n:
        return None
    point = successes / n
    err = _z(confidence) * math.sqrt(point * (1 - point) / n)
    low, high = _clip(point, err)
    return Bound(
        "Normal approximation",
        point,
        low,
        high,
        confidence,
        n,
        "asymptotic normality; not satisfied at small n, shown for contrast",
    )


def samples_needed(effect: float, confidence: float = 0.95) -> int:
    """Observations Hoeffding needs to resolve an edge of `effect` over a coin flip.

    Detecting a 5-point edge at 95% needs about 740 independent observations —
    the reason "not yet measurable" is often the honest answer.
    """
    if effect <= 0:
        return 0
    return math.ceil(math.log(2.0 / (1.0 - confidence)) / (2.0 * effect * effect))


def effective_sample_size(total: int, clusters: int, average_correlation: float) -> float:
    """Discount a sample for dependence: ``n_eff = n / (1 + (m − 1)ρ̄)``.

    The design-effect correction, with m the average cluster size. Observations
    taken on the same day across correlated stocks are partly one observation
    wearing several hats: a single market move resolves them together.
    """
    if total <= 0 or clusters <= 0:
        return 0.0
    m = total / clusters
    inflation = 1.0 + (m - 1.0) * max(0.0, min(1.0, average_correlation))
    return total / inflation


def bonferroni_threshold(n_tests: int, alpha: float = 0.05) -> float:
    """The two-sided |z| a single effect needs when `n_tests` were tried.

    Bonferroni rather than Benjamini–Hochberg: BH controls the share of
    survivors that are junk, the right tool for a portfolio of leads. When each
    survivor will be *used*, the question is whether any of them is spurious —
    family-wise error — and Bonferroni answers it without assuming anything
    about how the tests correlate.
    """
    if n_tests < 1:
        raise ValueError("n_tests must be at least 1")
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie strictly between 0 and 1")
    return _N.inv_cdf(1.0 - alpha / (2.0 * n_tests))


def expected_false_survivors(n_tests: int, threshold: float = 2.0) -> float:
    """How many of `n_tests` null effects clear |z| > `threshold` by luck alone."""
    if n_tests < 1:
        raise ValueError("n_tests must be at least 1")
    return n_tests * 2.0 * (1.0 - _N.cdf(abs(threshold)))


# ----------------------------------------------------------------------- Sharpe


def _moments(xs: Sequence[float]) -> tuple[float, float, float, float]:
    """(mean, sample sd, skewness, raw kurtosis) — kurtosis is 3 for a Gaussian."""
    n = len(xs)
    mu = sum(xs) / n
    m2 = sum((x - mu) ** 2 for x in xs) / n
    if m2 == 0:
        return mu, 0.0, 0.0, 3.0
    m3 = sum((x - mu) ** 3 for x in xs) / n
    m4 = sum((x - mu) ** 4 for x in xs) / n
    return mu, math.sqrt(m2 * n / (n - 1)), m3 / m2**1.5, m4 / (m2 * m2)


def _sharpe_se_factor(sr: float, skew: float, kurt: float) -> float:
    """``1 − γ₃·SR + (γ₄ − 1)/4 · SR²``: the non-normal Sharpe variance, times n − 1."""
    return max(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr, 1e-12)


def probabilistic_sharpe(
    sr: float, benchmark: float, n: int, skew: float = 0.0, kurt: float = 3.0
) -> float:
    """PSR: P(true Sharpe > `benchmark`), all Sharpes in *per-period* units.

    ``Φ((SR − SR*) √(n − 1) / √(1 − γ₃ SR + (γ₄ − 1)/4 · SR²))``. Negative skew
    and fat tails both widen the denominator, so the same Sharpe is less
    convincing from a crash-prone return stream than from a symmetric one.
    """
    return _N.cdf(
        (sr - benchmark) * math.sqrt(n - 1) / math.sqrt(_sharpe_se_factor(sr, skew, kurt))
    )


def expected_max_sharpe(n_trials: int, trial_variance: float) -> float:
    """Expected best Sharpe of `n_trials` zero-skill trials (per-period units).

    ``√V · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))``, the Euler–Mascheroni
    approximation to the expected maximum of N Gaussians used by Bailey &
    López de Prado (2014).
    """
    if n_trials <= 1 or trial_variance <= 0:
        return 0.0
    g = EULER_GAMMA
    return math.sqrt(trial_variance) * (
        (1 - g) * _N.inv_cdf(1 - 1 / n_trials) + g * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def min_track_record(
    sr: float,
    benchmark: float = 0.0,
    skew: float = 0.0,
    kurt: float = 3.0,
    confidence: float = 0.95,
) -> float | None:
    """Minimum Track Record Length, in periods, for PSR(benchmark) ≥ `confidence`.

    ``1 + (1 − γ₃ SR + (γ₄ − 1)/4 · SR²) · (z / (SR − SR*))²``. None when the
    Sharpe does not exceed the benchmark: no length of record would do.
    """
    if sr <= benchmark:
        return None
    z = _N.inv_cdf(confidence)
    return 1.0 + _sharpe_se_factor(sr, skew, kurt) * (z / (sr - benchmark)) ** 2


def sharpe_inference(
    returns: list[float], n_trials: int = 1, trial_sharpe_variance: float | None = None
) -> dict | None:
    """How much a daily return stream's Sharpe ratio can be believed.

    `n_trials` is how many strategies, parameter sets or symbols were tried
    before this one was chosen. `trial_sharpe_variance` is the variance of their
    **annualised** Sharpes; when it is not known it defaults to the variance a
    zero-skill trial's Sharpe estimate would have on this many observations
    (``252 / n`` annualised) — the assumption that every alternative tried was
    noise, which is the conservative one.

    ``kurtosis`` in the result is raw (3 for a Gaussian), as the PSR formula
    uses it. None below 60 observations or when returns have no variance.
    """
    n = len(returns)
    if n < SHARPE_MIN_OBSERVATIONS:
        return None
    mu, sd, skew, kurt = _moments(returns)
    if sd == 0:
        return None
    sr = mu / sd
    psr = probabilistic_sharpe(sr, 0.0, n, skew, kurt)

    dsr = None
    if n_trials > 1:
        v_annual = trial_sharpe_variance if trial_sharpe_variance is not None else TRADING_DAYS / n
        sr0 = expected_max_sharpe(n_trials, v_annual / TRADING_DAYS)
        dsr = probabilistic_sharpe(sr, sr0, n, skew, kurt)

    mtrl = min_track_record(sr, 0.0, skew, kurt)
    annual = sr * math.sqrt(TRADING_DAYS)

    if psr >= 0.95:
        reading = (
            f"Sharpe {annual:.2f} over {n} sessions: {psr:.0%} probability the true "
            "Sharpe is above zero, after allowing for skew and fat tails"
        )
    elif mtrl is not None:
        reading = (
            f"Sharpe {annual:.2f} over {n} sessions is not yet distinguishable from zero "
            f"({psr:.0%} probability it is positive); at this Sharpe it would take about "
            f"{math.ceil(mtrl)} sessions to tell"
        )
    else:
        reading = (
            f"Sharpe {annual:.2f} over {n} sessions: no evidence of positive risk-adjusted return"
        )
    if dsr is not None:
        reading += (
            f". Deflated for {n_trials} trials, the probability falls to {dsr:.0%}"
            if dsr < psr
            else f". Deflating for {n_trials} trials leaves it at {dsr:.0%}"
        )

    return {
        "sharpe_annual": round(annual, 4),
        "sharpe_daily": round(sr, 6),
        "observations": n,
        "psr_vs_zero": round(psr, 4),
        "dsr": None if dsr is None else round(dsr, 4),
        "min_track_record_sessions": None if mtrl is None else math.ceil(mtrl),
        "skewness": round(skew, 4),
        "kurtosis": round(kurt, 4),
        "reading": reading,
    }
