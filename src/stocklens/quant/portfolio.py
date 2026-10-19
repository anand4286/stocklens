"""How many bets a basket is really making, and five ways to weight it.

The claim this module tests
---------------------------
A watchlist of five names reads as five bets. It usually is not. In equities
one eigenvalue of the correlation matrix -- the market -- typically sits far
above the Marchenko-Pastur noise edge while the rest are inside the band, so
five names drawn from that market are closer to one bet at five times the
size. This module measures that, and then shows how the standard
construction rules redistribute risk across the same names.

StockLens is read-only analytics: nothing here is a recommendation or an
order. The weights exist to make a structural point visible -- how much of a
basket's risk is one thing, and how different rules answer that.

Effective number of bets (Meucci 2009)
--------------------------------------
Rotate the weights into the principal basis of the covariance matrix,
``w~ = V'w``. Each principal direction contributes ``w~_k^2 lambda_k`` to total
variance; normalised, those contributions are a probability distribution over
*uncorrelated* directions, and the effective number of bets is the exponential
of its Shannon entropy, ``exp(-sum p log p)``. Equal loading on N independent
directions gives N; all loading on one gives exactly 1, however many tickers.
Failure mode: principal directions are only identified up to rotation when
eigenvalues are tied, and the count inherits every error in the small
eigenvalues -- hence it is computed on the shrunk matrix.

Covariance choice
-----------------
Minimum variance, risk parity, HRP's variances, every volatility, every risk
contribution and every bet count use the Ledoit-Wolf shrunk covariance: the
first two invert or nearly invert the matrix, and the rest should be
consistent with them. HRP's *clustering* uses the sample correlation, because
the tree is meant to describe the observed co-movement, and the shrinkage
target (one common correlation) carries no hierarchy at all.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from .covariance import (
    average_correlation,
    ledoit_wolf_shrinkage,
    sample_covariance,
    spectrum,
    to_correlation,
)
from .linalg import Matrix, Vector, condition_number, jacobi_eigen, nearest_psd, quadratic_form

TRADING_DAYS = 252
MIN_OBSERVATIONS = 60
MAX_SYMBOLS = 25

_MAX_ITERATIONS = 20_000
_TOLERANCE = 1e-12


# ---------------------------------------------------------------- diagnostics


def effective_bets(weights: Vector, covariance: Matrix) -> float:
    """Meucci's entropy-based count of independent exposures.

    Accepts a covariance or a correlation matrix (on a correlation matrix it
    measures the vol-equalised book). Returns 1.0 for a degenerate input: a
    book with one position, or no variance to distribute, genuinely is one bet.
    """
    size = len(weights)
    if size == 0:
        return 0.0
    if size == 1:
        return 1.0
    values, vectors = jacobi_eigen(nearest_psd(covariance))
    rotated = [sum(vectors[i][k] * weights[i] for i in range(size)) for k in range(size)]
    contributions = [max(rotated[k] ** 2 * values[k], 0.0) for k in range(size)]
    total = sum(contributions)
    if total <= 0:
        return 1.0
    distribution = [c / total for c in contributions if c > 0]
    return math.exp(-sum(p * math.log(p) for p in distribution))


def risk_contributions(weights: Vector, covariance: Matrix) -> Vector:
    """Each position's share of total portfolio variance.

    ``RC_i = w_i (Sigma w)_i / (w' Sigma w)``. These sum to one by Euler's
    theorem on the degree-one homogeneous volatility function, which makes
    them a decomposition rather than a set of unrelated numbers: a position's
    contribution includes its covariance with everything else, so a small
    weight in a highly correlated name can carry more risk than a large weight
    in an uncorrelated one.
    """
    size = len(weights)
    total_variance = quadratic_form(weights, covariance)
    if total_variance <= 0:
        return [0.0] * size
    marginal = [sum(covariance[i][j] * weights[j] for j in range(size)) for i in range(size)]
    return [weights[i] * marginal[i] / total_variance for i in range(size)]


def diversification_ratio(weights: Vector, covariance: Matrix) -> float:
    """Weighted average volatility over portfolio volatility (Choueifaty 2008).

    One for a book of perfectly correlated names; sqrt(N) for N uncorrelated
    equal-vol names equally weighted. The gap above one is what correlation
    below one is buying.
    """
    sd = [math.sqrt(max(covariance[i][i], 0.0)) for i in range(len(weights))]
    port = math.sqrt(max(quadratic_form(weights, covariance), 0.0))
    if port <= 0:
        return 1.0
    return sum(w * s for w, s in zip(weights, sd, strict=True)) / port


# ---------------------------------------------------------------- construction


def equal_weight(covariance: Matrix) -> Vector:
    """1/N. The benchmark every other rule must beat out of sample, and
    frequently does not (DeMiguel, Garlappi & Uppal 2009): it estimates nothing,
    so it has no estimation error. Failure mode: it ignores risk entirely, so
    the most volatile name dominates the portfolio's variance."""
    size = len(covariance)
    return [1.0 / size] * size


def inverse_volatility(covariance: Matrix) -> Vector:
    """Weights proportional to 1/sigma_i. Equalises standalone risk using only
    the diagonal, the best-estimated part of the matrix. Failure mode: blind
    to correlation -- three near-duplicate names get three full allocations."""
    inv = [1.0 / math.sqrt(covariance[i][i]) for i in range(len(covariance))]
    total = sum(inv)
    return [x / total for x in inv]


def _project_simplex(vector: Vector) -> Vector:
    """Euclidean projection onto {w >= 0, sum w = 1} (Duchi et al. 2008)."""
    ordered = sorted(vector, reverse=True)
    cumulative, theta = 0.0, 0.0
    for k, value in enumerate(ordered, start=1):
        cumulative += value
        candidate = (cumulative - 1.0) / k
        if value - candidate > 0:
            theta = candidate
    return [max(v - theta, 0.0) for v in vector]


def minimum_variance(covariance: Matrix) -> Vector:
    """Long-only minimum variance: min w' Sigma w subject to w >= 0, sum w = 1.

    Solved by accelerated projected gradient (FISTA, step 1/lambda_max) onto
    the simplex, which is globally convergent for this convex problem. The
    only rule here that uses expected *co*-movement to its full extent, and so
    the one most exposed to estimation error: on a raw sample matrix it loads
    on whichever direction the noise makes look quiet. Shrinkage and the
    long-only constraint (Jagannathan & Ma 2003 show the constraint itself
    acts as shrinkage) are the two guards. Failure mode that remains: corner
    solutions -- a handful of low-vol names take everything and the rest get
    zero, which is concentration in weight even when variance is minimal.
    """
    size = len(covariance)
    lam_max = jacobi_eigen(covariance)[0][0]
    if lam_max <= 0:
        return equal_weight(covariance)
    step = 1.0 / lam_max
    w = equal_weight(covariance)
    y, t = w[:], 1.0
    for _ in range(_MAX_ITERATIONS):
        grad = [sum(covariance[i][j] * y[j] for j in range(size)) for i in range(size)]
        nxt = _project_simplex([y[i] - step * grad[i] for i in range(size)])
        t_next = (1.0 + math.sqrt(1.0 + 4.0 * t * t)) / 2.0
        y = [nxt[i] + ((t - 1.0) / t_next) * (nxt[i] - w[i]) for i in range(size)]
        change = max(abs(nxt[i] - w[i]) for i in range(size))
        w, t = nxt, t_next
        if change < _TOLERANCE:
            break
    total = sum(w)
    return [x / total for x in w]


def risk_parity(covariance: Matrix, budgets: Vector | None = None) -> Vector:
    """Equal risk contribution, long only.

    Cyclical coordinate descent on ``min 0.5 y'Sigma y - sum b_i log y_i``
    (Griveau-Billion, Richard & Roncalli 2013); the minimiser has
    ``y_i (Sigma y)_i = b_i`` for every i, so normalising y gives weights whose
    risk contributions equal the budgets. Each coordinate update is the
    positive root of a quadratic, so iterates stay strictly positive and no
    projection is needed. Uses the matrix without inverting it, which makes it
    much less sensitive to the small eigenvalues than minimum variance.
    Failure mode: "equal risk" is equal *estimated* risk; when one factor
    dominates, equalising contributions still leaves the book one bet.
    """
    size = len(covariance)
    b = budgets or [1.0 / size] * size
    y = [1.0 / math.sqrt(covariance[i][i]) for i in range(size)]
    scale = math.sqrt(quadratic_form(y, covariance))
    y = [v / scale for v in y]
    for _ in range(_MAX_ITERATIONS):
        biggest = 0.0
        for i in range(size):
            c = sum(covariance[i][j] * y[j] for j in range(size) if j != i)
            a = covariance[i][i]
            new = (-c + math.sqrt(c * c + 4.0 * a * b[i])) / (2.0 * a)
            biggest = max(biggest, abs(new - y[i]) / max(abs(new), 1e-300))
            y[i] = new
        if biggest < _TOLERANCE:
            break
    total = sum(y)
    return [v / total for v in y]


def correlation_distance(correlation: Matrix) -> Matrix:
    """``d_ij = sqrt((1 - rho_ij) / 2)``: 0 for identical, 1 for opposite.

    A proper metric (it is half the Euclidean distance between standardised
    return vectors), which is what makes the clustering below meaningful.
    """
    return [[math.sqrt(max(0.0, (1.0 - r) / 2.0)) for r in row] for row in correlation]


def single_linkage(distance: Matrix) -> list[list[float]]:
    """Agglomerative single-linkage clustering in the SciPy linkage format.

    Each row is ``[i, j, distance, size]``: clusters i and j merged at that
    distance into a new cluster with id ``N + row``. Single linkage merges on
    the closest pair between clusters, which is what López de Prado uses; its
    failure mode is "chaining", long straggly clusters, which matters less here
    because only the leaf order is used downstream.
    """
    size = len(distance)
    clusters: dict[int, list[int]] = {i: [i] for i in range(size)}
    rows: list[list[float]] = []
    next_id = size
    while len(clusters) > 1:
        ids = sorted(clusters)
        best = (math.inf, -1, -1)
        for a_pos, a in enumerate(ids):
            for b in ids[a_pos + 1:]:
                d = min(distance[i][j] for i in clusters[a] for j in clusters[b])
                if d < best[0]:
                    best = (d, a, b)
        d, a, b = best
        clusters[next_id] = clusters.pop(a) + clusters.pop(b)
        rows.append([float(a), float(b), d, float(len(clusters[next_id]))])
        next_id += 1
    return rows


def quasi_diagonal_order(linkage: list[list[float]], size: int) -> list[int]:
    """Leaf order of the dendrogram, so similar names sit next to each other.

    Reordering the covariance matrix this way concentrates large covariances
    near the diagonal ("quasi-diagonalisation"), which is what lets recursive
    bisection treat adjacent blocks as roughly independent.
    """
    if size == 1 or not linkage:
        return list(range(size))

    def expand(node: int) -> list[int]:
        if node < size:
            return [node]
        left, right = linkage[node - size][:2]
        return expand(int(left)) + expand(int(right))

    return expand(size + len(linkage) - 1)


def _cluster_variance(covariance: Matrix, members: list[int]) -> float:
    inv = [1.0 / covariance[i][i] for i in members]
    total = sum(inv)
    w = [x / total for x in inv]
    sub = [[covariance[i][j] for j in members] for i in members]
    return quadratic_form(w, sub)


def hierarchical_risk_parity(
    covariance: Matrix, correlation: Matrix
) -> tuple[Vector, list[int], list[list[float]]]:
    """Hierarchical Risk Parity (López de Prado 2016).

    1. Tree: single linkage on the Euclidean distance between columns of the
       correlation-distance matrix (a distance of distances, as in the paper:
       two names are close if they relate to *everything else* similarly).
    2. Quasi-diagonalise: order names by the dendrogram's leaves.
    3. Recursive bisection: split the ordered list in half, give each half its
       inverse-variance cluster variance, and allocate
       ``alpha = 1 - V_left / (V_left + V_right)`` to the left.

    Never inverts the covariance matrix, so it is stable where minimum
    variance is not, and it uses correlation structure where inverse
    volatility ignores it. Failure modes: bisection splits at the list
    midpoint, not at the tree's own branches, so a natural cluster can be cut
    in two; and the tree is itself estimated -- a small change in correlations
    can reorder leaves and move weights discontinuously.

    Returns ``(weights, order, linkage)``.
    """
    size = len(covariance)
    dist = correlation_distance(correlation)
    tilde = [
        [math.sqrt(sum((dist[k][i] - dist[k][j]) ** 2 for k in range(size))) for j in range(size)]
        for i in range(size)
    ]
    linkage = single_linkage(tilde)
    order = quasi_diagonal_order(linkage, size)

    weights = [1.0] * size
    stack = [order]
    while stack:
        items = stack.pop()
        if len(items) < 2:
            continue
        half = len(items) // 2
        left, right = items[:half], items[half:]
        v_left = _cluster_variance(covariance, left)
        v_right = _cluster_variance(covariance, right)
        alpha = 1.0 - v_left / (v_left + v_right) if v_left + v_right > 0 else 0.5
        for i in left:
            weights[i] *= alpha
        for i in right:
            weights[i] *= 1.0 - alpha
        stack.extend([left, right])
    total = sum(weights)
    return [w / total for w in weights], order, linkage


# ---------------------------------------------------------------- analysis


def _log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]


def _clean(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


_METHODS: dict[str, tuple[str, str]] = {
    "equal": ("Equal weight", "1/N in every name; estimates nothing, so nothing to get wrong."),
    "inverse_vol": (
        "Inverse volatility",
        "Weight proportional to 1/volatility; equalises standalone risk, ignores correlation.",
    ),
    "min_variance": (
        "Minimum variance",
        "Long-only weights that minimise variance on the shrunk covariance; prone to corners.",
    ),
    "risk_parity": (
        "Equal risk contribution",
        "Every name contributes the same share of portfolio variance.",
    ),
    "hrp": (
        "Hierarchical risk parity",
        "Cluster by correlation, then split risk recursively down the tree.",
    ),
}


def analyse(closes_by_symbol: dict[str, list[float]]) -> dict | None:
    """Correlation structure, shrinkage, spectrum and five weighting rules.

    ``closes_by_symbol`` holds daily closes oldest-first. Symbols with
    non-positive or non-finite closes, fewer than ``MIN_OBSERVATIONS + 1``
    closes, or zero return variance are dropped; at most ``MAX_SYMBOLS`` are
    kept in input order. The survivors are tail-aligned to a common length and
    converted to log returns. Returns None if fewer than two symbols survive
    or the common window has fewer than ``MIN_OBSERVATIONS`` returns.
    """
    usable: dict[str, list[float]] = {}
    for symbol, closes in closes_by_symbol.items():
        if len(usable) >= MAX_SYMBOLS:
            break
        if len(closes) < MIN_OBSERVATIONS + 1:
            continue
        if not all(isinstance(c, int | float) and math.isfinite(c) and c > 0 for c in closes):
            continue
        usable[symbol] = [float(c) for c in closes]
    if len(usable) < 2:
        return None

    length = min(len(c) for c in usable.values())
    returns = {s: _log_returns(c[-length:]) for s, c in usable.items()}
    returns = {s: r for s, r in returns.items() if max(r) - min(r) > 0}
    if len(returns) < 2:
        return None
    symbols = list(returns)
    observations = [returns[s] for s in symbols]
    n_obs = len(observations[0])
    if n_obs < MIN_OBSERVATIONS:
        return None

    sample = nearest_psd(sample_covariance(observations))
    sample_corr = to_correlation(sample)
    shrunk, intensity = ledoit_wolf_shrinkage(observations)
    report = spectrum(symbols, observations, intensity)

    hrp_w, order, linkage = hierarchical_risk_parity(shrunk, sample_corr)
    builders: dict[str, Callable[[], Vector]] = {
        "equal": lambda: equal_weight(shrunk),
        "inverse_vol": lambda: inverse_volatility(shrunk),
        "min_variance": lambda: minimum_variance(shrunk),
        "risk_parity": lambda: risk_parity(shrunk),
        "hrp": lambda: hrp_w,
    }

    methods: dict[str, dict] = {}
    for key, build in builders.items():
        w = build()
        variance = max(quadratic_form(w, shrunk), 0.0)
        rc = risk_contributions(w, shrunk)
        label, rule = _METHODS[key]
        methods[key] = {
            "label": label,
            "rule": rule,
            "weights": {s: round(x, 4) for s, x in zip(symbols, w, strict=True)},
            "volatility_annual": round(math.sqrt(variance * TRADING_DAYS), 4),
            "effective_bets": round(effective_bets(w, shrunk), 2),
            "risk_contributions": {s: round(x, 4) for s, x in zip(symbols, rc, strict=True)},
            "max_weight": round(max(w), 4),
            "diversification_ratio": round(diversification_ratio(w, shrunk), 3),
        }

    n = len(symbols)
    bets = methods["equal"]["effective_bets"]
    share = report.first_factor_share
    if report.signal_factors == 0:
        factor_text = "no factor clears the noise band"
    elif report.signal_factors == 1:
        factor_text = f"one common factor explains {share:.0%} of variance"
    else:
        factor_text = (
            f"{report.signal_factors} factors clear the noise band and the largest "
            f"explains {share:.0%} of variance"
        )
    reading = f"{n} names, {bets:.1f} effective bets held equally: {factor_text}."

    return {
        "symbols": symbols,
        "observations": n_obs,
        "correlation": [[round(v, 3) for v in row] for row in sample_corr],
        "shrinkage": {
            "intensity": round(intensity, 4),
            "target": "constant_correlation",
            "average_correlation": round(average_correlation(sample), 4),
        },
        "spectrum": {
            "eigenvalues": [round(v, 4) for v in report.eigenvalues],
            "mp_lower": round(report.lower_edge, 4),
            "mp_upper": round(report.upper_edge, 4),
            "q": round(report.q, 4),
            "signal_factors": report.signal_factors,
            "first_factor_share": round(share, 4),
            "explained": [round(v, 4) for v in report.explained],
            "condition_number_sample": _clean(condition_number(sample), 2),
            "condition_number_shrunk": _clean(condition_number(shrunk), 2),
        },
        "methods": methods,
        "hrp": {
            "order": [symbols[i] for i in order],
            "linkage": [[int(a), int(b), round(d, 4), int(c)] for a, b, d, c in linkage],
        },
        "reading": reading,
    }
