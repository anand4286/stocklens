import math

import pytest

from stocklens.analytics import risk


def test_max_drawdown_of_a_known_path():
    assert risk.max_drawdown([100, 120, 60, 90, 130]) == pytest.approx(-0.5)


def test_statistics_withhold_below_their_sample_floor():
    rets = [0.01, -0.01] * 5
    assert risk.annualised_vol(rets) is None
    assert risk.value_at_risk(rets) is None
    assert risk.sharpe(rets) is None
    assert risk.hurst([100.0] * 50) is None


def test_expected_shortfall_is_at_least_value_at_risk():
    rets = [math.sin(i * 1.7) * 0.02 for i in range(400)]
    assert risk.expected_shortfall(rets) >= risk.value_at_risk(rets)


def test_beta_of_a_series_against_itself_is_one():
    rets = [math.sin(i) * 0.01 for i in range(200)]
    assert risk.beta(rets, rets) == pytest.approx(1.0)
    assert risk.beta([2 * r for r in rets], rets) == pytest.approx(2.0)


def test_range_cone_widens_with_horizon_and_brackets_the_price():
    cone = risk.range_cone(100.0, 0.3)
    assert [c["sessions"] for c in cone] == [5, 21, 63]
    assert all(c["low"] < 100 < c["high"] for c in cone)
    assert cone[0]["sigma_pct"] < cone[1]["sigma_pct"] < cone[2]["sigma_pct"]


def test_variance_ratio_is_near_one_for_alternating_noise_free_trend():
    rets = [0.001] * 300
    # Constant returns have zero variance; the statistic must not divide by it.
    assert risk.variance_ratio(rets) is None
