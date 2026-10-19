"""Spectral linear algebra, implemented rather than imported.

Why by hand
-----------
StockLens is pure standard library, and for this module that is a feature
rather than a constraint: an approximation whose failure modes are visible in
the source is worth more on a site that explains its numbers than a black box
that is faster. Every routine below states where it is exact, where it is
iterative, and what it does when the input is degenerate -- which, for
covariance matrices estimated from a year of daily data, is most of the time.

What this is for
----------------
Several correlated names held side by side read as independent bets when each
is analysed on its own. Five semiconductor stocks are not five bets; they are
close to one bet at five times the size. Seeing that requires the covariance
matrix of returns, and reading a covariance matrix requires its spectrum.
Hence eigendecomposition, and hence the machinery for keeping an estimated
covariance matrix usable: real symmetric matrices have real eigenvalues and
orthogonal eigenvectors, but a *sample* covariance matrix routinely comes back
with tiny negative eigenvalues from floating-point error, which makes it
non-PSD and every risk number computed from it meaningless.

Conventions
-----------
Matrices are lists of row lists. Vectors are flat lists. Everything is
``float``. Sizes here are tens of assets, so O(n^3) algorithms cost
milliseconds and clarity wins over cleverness.
"""

from __future__ import annotations

import math

Matrix = list[list[float]]
Vector = list[float]

#: Jacobi sweeps stop when the largest off-diagonal element falls below this,
#: relative to the matrix scale. Well inside float64 resolution.
_TOLERANCE = 1e-14

#: A hard stop, in case a pathological input prevents convergence. Jacobi
#: converges quadratically; reaching this many single rotations means the input
#: is wrong rather than the method.
_MAX_ROTATIONS = 20_000


def identity(size: int) -> Matrix:
    return [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]


def transpose(matrix: Matrix) -> Matrix:
    return [list(column) for column in zip(*matrix, strict=True)]


def matmul(left: Matrix, right: Matrix) -> Matrix:
    """Ordinary matrix product. O(n^3), which is irrelevant at this size."""
    if not left or not right or len(left[0]) != len(right):
        raise ValueError("inner dimensions do not match")
    right_t = transpose(right)
    return [[sum(a * b for a, b in zip(row, col, strict=True)) for col in right_t] for row in left]


def matvec(matrix: Matrix, vector: Vector) -> Vector:
    if not matrix or len(matrix[0]) != len(vector):
        raise ValueError("dimensions do not match")
    return [sum(a * b for a, b in zip(row, vector, strict=True)) for row in matrix]


def quadratic_form(vector: Vector, matrix: Matrix) -> float:
    """``x' A x`` -- the scalar a covariance matrix exists to produce.

    Portfolio variance is exactly this: weights on both sides, covariance in
    the middle. Summing individual variances instead computes the same thing
    with every off-diagonal term deleted, which is the assumption that the
    positions are independent.
    """
    return sum(x * y for x, y in zip(vector, matvec(matrix, vector), strict=True))


def is_symmetric(matrix: Matrix, tolerance: float = 1e-9) -> bool:
    size = len(matrix)
    scale = max((abs(v) for row in matrix for v in row), default=0.0) or 1.0
    return all(
        abs(matrix[i][j] - matrix[j][i]) <= tolerance * scale
        for i in range(size)
        for j in range(i)
    )


def frobenius_norm(matrix: Matrix) -> float:
    """||A||_F -- the Euclidean norm of the entries, used to measure how far a
    shrunk or repaired matrix has moved from the original."""
    return math.sqrt(sum(value * value for row in matrix for value in row))


def jacobi_eigen(matrix: Matrix) -> tuple[Vector, Matrix]:
    """Eigenvalues and eigenvectors of a real symmetric matrix.

    The classical Jacobi method: repeatedly rotate away the largest
    off-diagonal element until the matrix is diagonal to machine precision.
    Chosen over the faster QR algorithm because it is unconditionally stable
    for symmetric matrices and returns eigenvectors orthogonal to working
    precision without a separate accumulation step. Correlation matrices are
    small; correctness is the scarce resource.

    Returns eigenvalues in descending order with the matching eigenvectors as
    *columns*: the first column is the direction of greatest variance.
    """
    size = len(matrix)
    if size == 0:
        return [], []
    if not is_symmetric(matrix):
        raise ValueError("eigendecomposition here is defined for symmetric matrices only")

    work = [row[:] for row in matrix]
    vectors = identity(size)
    scale = max(abs(v) for row in matrix for v in row) or 1.0

    for _ in range(_MAX_ROTATIONS):
        # Largest off-diagonal magnitude decides both the pivot and the stop.
        pivot_value, p, q = 0.0, 0, 1
        for i in range(size):
            row = work[i]
            for j in range(i + 1, size):
                if abs(row[j]) > pivot_value:
                    pivot_value, p, q = abs(row[j]), i, j
        if pivot_value <= _TOLERANCE * scale:
            break

        app, aqq, apq = work[p][p], work[q][q], work[p][q]
        # The rotation angle that annihilates the (p, q) element exactly.
        theta = (aqq - app) / (2.0 * apq)
        sign = 1.0 if theta >= 0 else -1.0
        t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
        c = 1.0 / math.sqrt(t * t + 1.0)
        s = t * c

        for k in range(size):
            akp, akq = work[k][p], work[k][q]
            work[k][p] = c * akp - s * akq
            work[k][q] = s * akp + c * akq
        for k in range(size):
            apk, aqk = work[p][k], work[q][k]
            work[p][k] = c * apk - s * aqk
            work[q][k] = s * apk + c * aqk
        for k in range(size):
            vkp, vkq = vectors[k][p], vectors[k][q]
            vectors[k][p] = c * vkp - s * vkq
            vectors[k][q] = s * vkp + c * vkq

    eigenvalues = [work[i][i] for i in range(size)]
    order = sorted(range(size), key=lambda i: eigenvalues[i], reverse=True)
    sorted_values = [eigenvalues[i] for i in order]
    sorted_vectors = [[vectors[row][i] for i in order] for row in range(size)]
    return sorted_values, sorted_vectors


def rebuild(values: Vector, vectors: Matrix) -> Matrix:
    """``V diag(values) V'``, symmetrised to remove last-bit asymmetry."""
    size = len(values)
    out = [
        [sum(vectors[i][k] * values[k] * vectors[j][k] for k in range(size)) for j in range(size)]
        for i in range(size)
    ]
    return [[(out[i][j] + out[j][i]) / 2.0 for j in range(size)] for i in range(size)]


def nearest_psd(matrix: Matrix, floor: float = 1e-12) -> Matrix:
    """The closest positive-semidefinite matrix, by eigenvalue clipping.

    A sample covariance matrix is PSD in theory and frequently not in
    practice: with few observations, or through accumulated floating-point
    error, the smallest eigenvalues come back slightly negative. That is not a
    rounding curiosity -- a negative eigenvalue means some portfolio has
    negative variance, so any risk number taken from the matrix is
    meaningless, and Cholesky fails outright.

    Clipping the spectrum at a small positive floor (relative to the largest
    eigenvalue) and rebuilding ``V L V'`` gives the nearest PSD matrix in the
    Frobenius norm (Higham 1988).
    """
    values, vectors = jacobi_eigen(matrix)
    if not values:
        return []
    threshold = floor * max(abs(values[0]), 1e-300)
    if all(value >= threshold for value in values):
        return [row[:] for row in matrix]
    return rebuild([max(value, threshold) for value in values], vectors)


def condition_number(matrix: Matrix) -> float | None:
    """lambda_max / lambda_min -- how much the matrix amplifies error when inverted.

    A large condition number is the quantitative form of "this matrix should
    not be inverted". It is why minimum-variance weights from a raw sample
    covariance matrix are famously unstable: the optimiser loads up on
    whichever direction the estimate happens to think is quiet, and that
    direction is usually noise. Returns ``inf`` for a singular matrix and
    None for an empty one.
    """
    values, _ = jacobi_eigen(matrix)
    if not values:
        return None
    largest = max(abs(value) for value in values)
    smallest = min(abs(value) for value in values)
    if smallest <= 1e-15 * max(largest, 1e-300):
        return math.inf
    return largest / smallest


def cholesky(matrix: Matrix) -> Matrix | None:
    """Lower-triangular ``L`` with ``L L' = A``, or None if A is not PD.

    Returning None rather than raising makes this usable as a positive-definite
    *test*: the factorisation exists exactly when the matrix is positive
    definite, so a failure is information rather than an error.
    """
    size = len(matrix)
    lower = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            total = sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                residual = matrix[i][i] - total
                if residual <= 0:
                    return None
                lower[i][j] = math.sqrt(residual)
            else:
                lower[i][j] = (matrix[i][j] - total) / lower[j][j]
    return lower


def solve_psd(matrix: Matrix, rhs: Vector) -> Vector | None:
    """Solve ``A x = b`` for positive-definite A, by Cholesky substitution.

    Preferred over forming an explicit inverse: inverting and multiplying
    costs more arithmetic and more precision than solving directly. None when
    A is not positive definite.
    """
    lower = cholesky(matrix)
    if lower is None:
        return None
    size = len(rhs)

    forward = [0.0] * size
    for i in range(size):
        forward[i] = (rhs[i] - sum(lower[i][k] * forward[k] for k in range(i))) / lower[i][i]

    solution = [0.0] * size
    for i in reversed(range(size)):
        total = sum(lower[k][i] * solution[k] for k in range(i + 1, size))
        solution[i] = (forward[i] - total) / lower[i][i]
    return solution


def explained_variance(eigenvalues: Vector) -> Vector:
    """Share of total variance carried by each eigenvalue.

    Negative eigenvalues are clipped to zero first. They are estimation error
    rather than variance, and letting them into the denominator would inflate
    every share above them.
    """
    positive = [max(value, 0.0) for value in eigenvalues]
    total = sum(positive)
    if total <= 0:
        return [0.0] * len(eigenvalues)
    return [value / total for value in positive]
