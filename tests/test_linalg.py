from __future__ import annotations

import math

import pytest

from stocklens.quant import linalg


def test_jacobi_recovers_known_eigenpairs():
    # [[2,1],[1,2]] has eigenvalues 3 and 1 with vectors (1,1)/sqrt2 and (1,-1)/sqrt2.
    values, vectors = linalg.jacobi_eigen([[2.0, 1.0], [1.0, 2.0]])
    assert values == pytest.approx([3.0, 1.0])
    first = [vectors[0][0], vectors[1][0]]
    assert abs(first[0]) == pytest.approx(1 / math.sqrt(2))
    assert first[0] == pytest.approx(first[1])


def test_jacobi_reconstructs_matrix_and_vectors_are_orthonormal():
    a = [[4.0, 1.0, 0.5], [1.0, 3.0, 0.2], [0.5, 0.2, 2.0]]
    values, vectors = linalg.jacobi_eigen(a)
    rebuilt = linalg.rebuild(values, vectors)
    for i in range(3):
        for j in range(3):
            assert rebuilt[i][j] == pytest.approx(a[i][j], abs=1e-10)
    gram = linalg.matmul(linalg.transpose(vectors), vectors)
    for i in range(3):
        for j in range(3):
            assert gram[i][j] == pytest.approx(1.0 if i == j else 0.0, abs=1e-10)
    assert values == sorted(values, reverse=True)


def test_jacobi_rejects_asymmetric():
    with pytest.raises(ValueError):
        linalg.jacobi_eigen([[1.0, 2.0], [0.0, 1.0]])


def test_nearest_psd_fixes_non_psd_matrix():
    bad = [[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]]
    assert min(linalg.jacobi_eigen(bad)[0]) < 0
    fixed = linalg.nearest_psd(bad)
    assert min(linalg.jacobi_eigen(fixed)[0]) >= 0
    assert linalg.is_symmetric(fixed)
    assert linalg.cholesky(bad) is None


def test_nearest_psd_leaves_psd_matrix_alone():
    good = [[2.0, 0.5], [0.5, 1.0]]
    assert linalg.nearest_psd(good) == good


def test_cholesky_and_solve():
    a = [[4.0, 2.0], [2.0, 3.0]]
    lower = linalg.cholesky(a)
    assert lower is not None
    product = linalg.matmul(lower, linalg.transpose(lower))
    assert product[0] == pytest.approx([4.0, 2.0])
    assert product[1] == pytest.approx([2.0, 3.0])
    x = linalg.solve_psd(a, [1.0, 2.0])
    assert x is not None
    assert linalg.matvec(a, x) == pytest.approx([1.0, 2.0])
    assert linalg.solve_psd([[1.0, 2.0], [2.0, 1.0]], [1.0, 1.0]) is None


def test_condition_number():
    assert linalg.condition_number([[4.0, 0.0], [0.0, 1.0]]) == pytest.approx(4.0)
    assert linalg.condition_number([[1.0, 1.0], [1.0, 1.0]]) == math.inf
    assert linalg.condition_number([]) is None


def test_explained_variance_clips_negatives():
    assert linalg.explained_variance([3.0, 1.0, -0.5]) == pytest.approx([0.75, 0.25, 0.0])
    assert linalg.explained_variance([0.0, 0.0]) == [0.0, 0.0]


def test_quadratic_form():
    assert linalg.quadratic_form([1.0, 2.0], [[1.0, 0.5], [0.5, 2.0]]) == pytest.approx(11.0)
