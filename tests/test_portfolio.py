from __future__ import annotations

import json
import math
import random

import pytest

from stocklens.quant import portfolio as pf
from stocklens.quant.linalg import quadratic_form


def diag(values):
    n = len(values)
    return [[values[i] if i == j else 0.0 for j in range(n)] for i in range(n)]


def factor_cov(vols, rho):
    n = len(vols)
    return [[vols[i] * vols[j] * (1.0 if i == j else rho) for j in range(n)] for i in range(n)]


def closes(n_symbols=5, n_days=260, seed=42, betas=None):
    rng = random.Random(seed)  # noqa: S311 - deterministic test data
    market = [rng.gauss(0.0003, 0.01) for _ in range(n_days)]
    betas = betas or [0.6 + 0.2 * i for i in range(n_symbols)]
    out = {}
    for k in range(n_symbols):
        price, path = 100.0, [100.0]
        idio = 0.006 + 0.003 * k
        for m in market:
            price *= math.exp(betas[k] * m + rng.gauss(0, idio))
            path.append(price)
        out[f"S{k}"] = path
    return out


def test_effective_bets_uncorrelated_equal_vol_is_n():
    n = 5
    assert pf.effective_bets([1 / n] * n, diag([0.04] * n)) == pytest.approx(n)


def test_effective_bets_perfectly_correlated_is_one():
    cov = factor_cov([0.2, 0.2, 0.2, 0.2], 1.0)
    assert pf.effective_bets([0.25] * 4, cov) == pytest.approx(1.0, abs=1e-6)


def test_risk_contributions_sum_to_one():
    cov = factor_cov([0.1, 0.2, 0.3], 0.4)
    rc = pf.risk_contributions([0.5, 0.3, 0.2], cov)
    assert sum(rc) == pytest.approx(1.0)


def test_risk_parity_equalises_contributions():
    cov = factor_cov([0.1, 0.2, 0.3, 0.15], 0.4)
    cov[0][1] = cov[1][0] = 0.1 * 0.2 * 0.8
    w = pf.risk_parity(cov)
    assert sum(w) == pytest.approx(1.0)
    assert all(x > 0 for x in w)
    for c in pf.risk_contributions(w, cov):
        assert c == pytest.approx(0.25, abs=1e-8)


def test_risk_parity_uncorrelated_is_inverse_vol():
    cov = diag([0.01, 0.04, 0.09])
    assert pf.risk_parity(cov) == pytest.approx(pf.inverse_volatility(cov), abs=1e-9)


def test_minimum_variance_beats_equal_weight_and_is_long_only():
    cov = factor_cov([0.1, 0.2, 0.3, 0.25], 0.5)
    w = pf.minimum_variance(cov)
    assert sum(w) == pytest.approx(1.0)
    assert all(x >= 0 for x in w)
    assert quadratic_form(w, cov) < quadratic_form(pf.equal_weight(cov), cov)


def test_minimum_variance_matches_closed_form_when_interior():
    cov = diag([0.01, 0.04, 0.09])
    inv = [100.0, 25.0, 100 / 9]
    expected = [x / sum(inv) for x in inv]
    assert pf.minimum_variance(cov) == pytest.approx(expected, abs=1e-8)


def test_minimum_variance_hits_corner():
    # A high-vol name highly correlated with a low-vol one should get zero.
    cov = [[0.01, 0.018], [0.018, 0.04]]
    w = pf.minimum_variance(cov)
    assert w[1] == pytest.approx(0.0, abs=1e-9)
    assert w[0] == pytest.approx(1.0)


def test_hrp_weights_positive_and_sum_to_one():
    cov = factor_cov([0.1, 0.2, 0.3, 0.15, 0.25], 0.3)
    corr = [[cov[i][j] / math.sqrt(cov[i][i] * cov[j][j]) for j in range(5)] for i in range(5)]
    w, order, linkage = pf.hierarchical_risk_parity(cov, corr)
    assert sum(w) == pytest.approx(1.0)
    assert all(x > 0 for x in w)
    assert sorted(order) == list(range(5))
    assert len(linkage) == 4
    assert linkage[-1][3] == 5


def test_hrp_clusters_correlated_pairs_together():
    corr = [
        [1.0, 0.9, 0.1, 0.1],
        [0.9, 1.0, 0.1, 0.1],
        [0.1, 0.1, 1.0, 0.9],
        [0.1, 0.1, 0.9, 1.0],
    ]
    cov = [[0.04 * c for c in row] for row in corr]
    w, order, _ = pf.hierarchical_risk_parity(cov, corr)
    assert {order[0], order[1]} in ({0, 1}, {2, 3})
    assert w == pytest.approx([0.25] * 4)


def test_correlation_distance():
    d = pf.correlation_distance([[1.0, -1.0], [-1.0, 1.0]])
    assert d[0][0] == 0.0
    assert d[0][1] == pytest.approx(1.0)


def test_analyse_none_below_floors():
    assert pf.analyse({}) is None
    assert pf.analyse({"A": closes(1)["S0"]}) is None
    short = closes(3, n_days=40)
    assert pf.analyse(short) is None
    flat = {"A": [100.0] * 200, "B": closes(1)["S0"]}
    assert pf.analyse(flat) is None


def test_analyse_drops_short_and_caps_symbols():
    data = closes(3)
    data["SHORT"] = [100.0] * 10
    result = pf.analyse(data)
    assert result is not None
    assert "SHORT" not in result["symbols"]
    many = closes(30, n_days=80, betas=[1.0] * 30)
    assert len(pf.analyse(many)["symbols"]) == pf.MAX_SYMBOLS


def test_analyse_well_formed():
    result = pf.analyse(closes(5))
    assert result is not None
    json.dumps(result, allow_nan=False)
    syms = result["symbols"]
    assert syms == [f"S{i}" for i in range(5)]
    assert result["observations"] == 260
    assert len(result["correlation"]) == 5
    assert 0.0 <= result["shrinkage"]["intensity"] <= 1.0
    assert result["shrinkage"]["target"] == "constant_correlation"
    spec = result["spectrum"]
    assert spec["eigenvalues"] == sorted(spec["eigenvalues"], reverse=True)
    assert spec["mp_lower"] < spec["mp_upper"]
    assert spec["signal_factors"] >= 1
    assert set(result["methods"]) == {"equal", "inverse_vol", "min_variance", "risk_parity", "hrp"}
    for m in result["methods"].values():
        assert set(m["weights"]) == set(syms)
        assert sum(m["weights"].values()) == pytest.approx(1.0, abs=1e-3)
        assert sum(m["risk_contributions"].values()) == pytest.approx(1.0, abs=1e-3)
        assert m["volatility_annual"] > 0
        assert 1.0 <= m["effective_bets"] <= 5.0
        assert m["diversification_ratio"] >= 1.0
    mv = result["methods"]["min_variance"]["volatility_annual"]
    assert all(mv <= m["volatility_annual"] + 1e-9 for m in result["methods"].values())
    rp = result["methods"]["risk_parity"]["risk_contributions"]
    assert max(rp.values()) - min(rp.values()) < 1e-3
    assert sorted(result["hrp"]["order"]) == sorted(syms)
    assert len(result["hrp"]["linkage"]) == 4
    assert result["reading"].startswith("5 names")
