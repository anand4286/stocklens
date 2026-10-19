"""Estimating a covariance matrix that is worth using.

The problem
-----------
Take 20 symbols and 250 daily returns each. The sample covariance matrix has
20 x 21 / 2 = 210 free parameters estimated from 5,000 observations, which
sounds comfortable and is not: the ratio that governs the error is
q = N/T = 0.08, and at that ratio the *smallest* eigenvalues of the sample
matrix are wrong by a large factor even though the matrix as a whole looks
reasonable.

This matters because every use of a covariance matrix is a use of its
spectrum. Portfolio variance loads on all of it; an optimiser loads almost
entirely on the small eigenvalues, which is why minimum-variance weights from
a raw sample matrix are famously unstable -- the optimiser finds whichever
direction the estimate believes is quiet, and at q = 0.08 that direction is
noise.

Two corrections, from different traditions
------------------------------------------
**Shrinkage** (Ledoit & Wolf 2004, "Honey, I shrunk the sample covariance
matrix") pulls the sample matrix toward a structured target -- here, constant
correlation -- by an amount chosen to minimise expected squared error. It is a
bias-variance trade made explicitly: accept bias toward a wrong-but-stable
target in exchange for a large variance reduction. The optimal intensity is
estimable from the data itself, so it is not a tuned knob.

**Random matrix theory** (Marchenko & Pastur 1967) says something stronger. If
the true correlation were identity -- no structure at all -- the sample
eigenvalues would still spread out, and their limiting distribution is
supported on [(1-sqrt q)^2, (1+sqrt q)^2]. Any eigenvalue inside that band is
indistinguishable from what pure noise produces. Only eigenvalues above the
upper edge carry information. That turns "how many real factors are in this
basket?" from a scree-plot judgement call into a stated test.

Everything here is descriptive: StockLens reads the matrix, it does not trade
on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .linalg import (
    Matrix,
    Vector,
    condition_number,
    explained_variance,
    jacobi_eigen,
    nearest_psd,
    rebuild,
)

#: Below this many observations per series the covariance estimate is not
#: worth correcting -- it is worth refusing.
MIN_OBSERVATIONS = 60


def _demean(series: list[float]) -> list[float]:
    mean = sum(series) / len(series)
    return [value - mean for value in series]


def align(
    series_by_symbol: dict[str, list[float]], min_observations: int = MIN_OBSERVATIONS
) -> tuple[list[str], list[list[float]]]:
    """Trim every series to a common length, newest-aligned.

    Covariance across series of different lengths is not a covariance -- it
    measures the calendar. Aligning on the most recent observations keeps the
    estimate about the current market. Series shorter than the floor are
    dropped rather than allowed to truncate everyone else. Input order is
    preserved so callers control presentation. Returns ``([], [])`` when fewer
    than two series survive.
    """
    usable = {s: v for s, v in series_by_symbol.items() if len(v) >= min_observations}
    if len(usable) < 2:
        return [], []
    length = min(len(values) for values in usable.values())
    symbols = list(usable)
    return symbols, [list(usable[symbol][-length:]) for symbol in symbols]


def sample_covariance(observations: list[list[float]]) -> Matrix:
    """Unbiased sample covariance of row-wise series.

    Divides by T-1 rather than T: with the mean estimated from the same data,
    dividing by T understates every variance, systematically.
    """
    n_series = len(observations)
    n_obs = len(observations[0])
    centred = [_demean(series) for series in observations]
    denominator = n_obs - 1
    out = [[0.0] * n_series for _ in range(n_series)]
    for i in range(n_series):
        for j in range(i, n_series):
            value = sum(a * b for a, b in zip(centred[i], centred[j], strict=True)) / denominator
            out[i][j] = out[j][i] = value
    return out


def to_correlation(covariance: Matrix) -> Matrix:
    """Standardise to unit diagonal.

    Spectral statements are made on the correlation matrix, because the
    Marchenko-Pastur bounds assume standardised variables. On a covariance
    matrix the eigenvalues carry the scale of whichever asset is most
    volatile, and the noise band means nothing. A zero-variance series gets a
    zero row (and a one on the diagonal) rather than a division by zero.
    """
    size = len(covariance)
    sd = [math.sqrt(covariance[i][i]) if covariance[i][i] > 0 else 0.0 for i in range(size)]
    return [
        [
            1.0
            if i == j
            else (covariance[i][j] / (sd[i] * sd[j]) if sd[i] > 0 and sd[j] > 0 else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]


def average_correlation(covariance: Matrix) -> float:
    """Mean off-diagonal correlation -- the single number the shrinkage target keeps."""
    corr = to_correlation(covariance)
    size = len(corr)
    pairs = [corr[i][j] for i in range(size) for j in range(i + 1, size)]
    return sum(pairs) / len(pairs) if pairs else 0.0


def marchenko_pastur_edges(n_series: int, n_obs: int) -> tuple[float, float]:
    """The interval a pure-noise correlation spectrum occupies.

    ``[(1-sqrt q)^2, (1+sqrt q)^2]`` for ``q = N/T``. An eigenvalue inside this
    band is what independent series produce by chance; it is not evidence of a
    factor. The result is asymptotic (N, T large at fixed q) and, at the sizes
    used here, the top edge is soft: an eigenvalue a hair above it is weak
    evidence, not proof.
    """
    if n_obs <= 0 or n_series <= 0:
        return 0.0, 0.0
    root = math.sqrt(n_series / n_obs)
    return (1.0 - root) ** 2, (1.0 + root) ** 2


def constant_correlation_target(covariance: Matrix) -> Matrix:
    """Every pair shares the average correlation; variances untouched."""
    size = len(covariance)
    sd = [math.sqrt(max(covariance[i][i], 0.0)) for i in range(size)]
    rbar = average_correlation(covariance)
    return [
        [covariance[i][i] if i == j else rbar * sd[i] * sd[j] for j in range(size)]
        for i in range(size)
    ]


def ledoit_wolf_shrinkage(observations: list[list[float]]) -> tuple[Matrix, float]:
    """Shrink the sample covariance toward a constant-correlation target.

    Returns the shrunk matrix and the intensity used, in [0, 1]. The intensity
    is the estimated minimiser of expected squared error between the estimate
    and the true matrix, ``delta = clip((pi - rho) / gamma / T, 0, 1)``:

    * ``pi``    -- summed asymptotic variance of the sample covariance entries
      (how noisy the sample is);
    * ``rho``   -- summed asymptotic covariance between the target and the
      sample entries (how much of that noise the target shares);
    * ``gamma`` -- squared distance between target and sample (how wrong the
      target is).

    This is the full estimator including ``rho``. A common shortcut uses
    ``pi`` alone, which biases the intensity upward and at
    small N saturates at 1 -- replacing the data entirely with the target and
    deleting exactly the dispersion of correlations one wanted to see. With
    ``rho`` in place, intensity near 1 still means "the sample carries little
    beyond the average correlation", and should be read as a warning.

    Moments use the 1/T normalisation of the original paper; the matrix that
    is shrunk is the unbiased (T-1) sample covariance.
    """
    n_series = len(observations)
    n_obs = len(observations[0])
    sample = sample_covariance(observations)
    if n_series < 2:
        return sample, 0.0
    centred = [_demean(series) for series in observations]

    s = [[sum(a * b for a, b in zip(centred[i], centred[j], strict=True)) / n_obs
          for j in range(n_series)] for i in range(n_series)]
    sd = [math.sqrt(s[i][i]) if s[i][i] > 0 else 0.0 for i in range(n_series)]
    pairs = [
        s[i][j] / (sd[i] * sd[j])
        for i in range(n_series)
        for j in range(i + 1, n_series)
        if sd[i] > 0 and sd[j] > 0
    ]
    rbar = sum(pairs) / len(pairs) if pairs else 0.0

    squares = [[x * x - s[i][i] for x in centred[i]] for i in range(n_series)]
    pi_mat = [[0.0] * n_series for _ in range(n_series)]
    theta = [[0.0] * n_series for _ in range(n_series)]  # theta[i][j] = theta_{ii,ij}
    for i in range(n_series):
        for j in range(n_series):
            dev = [a * b - s[i][j] for a, b in zip(centred[i], centred[j], strict=True)]
            pi_mat[i][j] = sum(d * d for d in dev) / n_obs
            theta[i][j] = sum(q * d for q, d in zip(squares[i], dev, strict=True)) / n_obs

    pi = sum(sum(row) for row in pi_mat)
    rho = sum(pi_mat[i][i] for i in range(n_series))
    for i in range(n_series):
        for j in range(n_series):
            if i == j or sd[i] <= 0 or sd[j] <= 0:
                continue
            rho += (rbar / 2.0) * (
                (sd[j] / sd[i]) * theta[i][j] + (sd[i] / sd[j]) * theta[j][i]
            )

    target_s = [
        [s[i][i] if i == j else rbar * sd[i] * sd[j] for j in range(n_series)]
        for i in range(n_series)
    ]
    gamma = sum(
        (target_s[i][j] - s[i][j]) ** 2 for i in range(n_series) for j in range(n_series)
    )
    if gamma <= 0:
        return sample, 0.0
    intensity = max(0.0, min(1.0, (pi - rho) / gamma / n_obs))

    target = constant_correlation_target(sample)
    shrunk = [
        [intensity * target[i][j] + (1 - intensity) * sample[i][j] for j in range(n_series)]
        for i in range(n_series)
    ]
    return nearest_psd(shrunk), intensity


@dataclass
class SpectrumReport:
    """What a sample correlation matrix's eigenvalues say about the basket."""

    symbols: list[str]
    observations: int
    eigenvalues: Vector
    explained: Vector
    lower_edge: float
    upper_edge: float
    signal_factors: int
    """Eigenvalues above the Marchenko-Pastur upper edge -- the count of
    directions not explainable as sampling noise."""
    first_factor_share: float
    """Share of total variance in the largest eigenvector. In equities this is
    almost always the market itself, and it is why a basket of single names is
    closer to one bet than to many."""
    shrinkage_intensity: float
    condition: float | None
    loadings: Matrix = field(default_factory=list)

    @property
    def q(self) -> float:
        return len(self.symbols) / self.observations if self.observations else 0.0

    @property
    def interpretation(self) -> str:
        total = len(self.eigenvalues)
        if not total:
            return "no spectrum to read"
        return (
            f"{self.signal_factors} of {total} directions clear the Marchenko-Pastur "
            f"noise band; the largest factor explains {self.first_factor_share * 100:.0f}% "
            "of total variance."
        )


def spectrum(
    symbols: list[str], observations: list[list[float]], shrinkage_intensity: float = 0.0
) -> SpectrumReport:
    """Spectral read of the *sample* correlation matrix.

    The sample, not the shrunk, matrix: the Marchenko-Pastur band describes how
    a sample correlation spectrum spreads under noise, so comparing a shrunk
    spectrum (already compressed toward its mean) against it would understate
    the number of factors.
    """
    n_series, n_obs = len(symbols), len(observations[0])
    correlation = nearest_psd(to_correlation(sample_covariance(observations)))
    values, vectors = jacobi_eigen(correlation)
    lower, upper = marchenko_pastur_edges(n_series, n_obs)
    shares = explained_variance(values)
    return SpectrumReport(
        symbols=symbols,
        observations=n_obs,
        eigenvalues=values,
        explained=shares,
        lower_edge=lower,
        upper_edge=upper,
        signal_factors=sum(1 for value in values if value > upper),
        first_factor_share=shares[0] if shares else 0.0,
        shrinkage_intensity=shrinkage_intensity,
        condition=condition_number(correlation),
        loadings=[[vectors[i][k] for k in range(min(3, n_series))] for i in range(n_series)],
    )


def denoise(correlation: Matrix, n_obs: int) -> Matrix:
    """Replace noise-band eigenvalues with their average, keeping the trace.

    Eigenvalue clipping in the sense of Laloux et al. (1999): directions inside
    the Marchenko-Pastur band carry no information, so distinguishing between
    them is fitting noise. Collapsing them to a common value preserves total
    variance while removing the spurious structure an optimiser would exploit.
    Failure mode: with only a handful of assets the band is wide and almost
    everything is "noise", so the result drifts toward a one-factor matrix.
    """
    size = len(correlation)
    if size < 2 or n_obs <= 0:
        return [row[:] for row in correlation]

    values, vectors = jacobi_eigen(correlation)
    _, upper = marchenko_pastur_edges(size, n_obs)
    noise = {i for i, value in enumerate(values) if value <= upper}
    if not noise:
        return [row[:] for row in correlation]

    replacement = sum(values[i] for i in noise) / len(noise)
    adjusted = [replacement if i in noise else values[i] for i in range(size)]
    rebuilt = rebuild(adjusted, vectors)
    # Restore the unit diagonal a correlation matrix must have.
    scale = [math.sqrt(rebuilt[i][i]) if rebuilt[i][i] > 0 else 1.0 for i in range(size)]
    return [[rebuilt[i][j] / (scale[i] * scale[j]) for j in range(size)] for i in range(size)]
