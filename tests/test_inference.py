# ruff: noqa: S311 -- seeded simulation, not cryptography.
import random

import pytest

from stocklens.quant import inference as inf


def test_hoeffding_is_wider_than_wilson():
    for successes, n in ((18, 30), (60, 100), (3, 40)):
        h = inf.hoeffding_interval(successes, n)
        w = inf.wilson_interval(successes, n)
        assert h.width > w.width
        assert 0 <= w.low <= w.point <= w.high <= 1


def test_intervals_withhold_on_empty_samples():
    assert inf.hoeffding_interval(0, 0) is None
    assert inf.wilson_interval(0, 0) is None
    assert inf.wilson_interval(5, 3) is None


def test_wilson_does_not_collapse_at_zero_successes():
    w = inf.wilson_interval(0, 20)
    assert w.low == 0 and w.high > 0.1
    assert inf.normal_interval(0, 20).width == 0
    assert set(w.as_dict()) >= {"low", "high", "point", "method"}


def test_bernstein_tighter_than_hoeffding_far_from_half():
    assert inf.bernstein_interval(95, 100).width < inf.hoeffding_interval(95, 100).width


def test_samples_needed_and_effective_size():
    assert inf.samples_needed(0.05) == pytest.approx(738, abs=2)
    assert inf.effective_sample_size(100, 100, 0.5) == 100
    assert inf.effective_sample_size(100, 10, 0.5) < 100


def test_bonferroni_and_false_survivors():
    assert inf.bonferroni_threshold(1) == pytest.approx(1.96, abs=0.01)
    assert inf.bonferroni_threshold(11) > inf.bonferroni_threshold(1)
    assert inf.expected_false_survivors(11) == pytest.approx(0.5, abs=0.02)
    with pytest.raises(ValueError):
        inf.bonferroni_threshold(0)


def _returns(mean: float, n: int = 750, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    noise = [rng.gauss(0, 0.01) for _ in range(n)]
    shift = sum(noise) / n
    return [x - shift + mean for x in noise]


def test_psr_is_half_at_zero_mean_and_rises_with_sharpe():
    zero = inf.sharpe_inference(_returns(0.0))
    assert zero["psr_vs_zero"] == pytest.approx(0.5, abs=0.01)
    assert zero["min_track_record_sessions"] is None
    psrs = [inf.sharpe_inference(_returns(m))["psr_vs_zero"] for m in (0.0, 0.0003, 0.0008)]
    assert psrs == sorted(psrs) and psrs[-1] > psrs[0]


def test_dsr_falls_below_psr_as_trials_increase():
    rets = _returns(0.0008)
    one = inf.sharpe_inference(rets)
    assert one["dsr"] is None
    dsrs = [inf.sharpe_inference(rets, n_trials=k)["dsr"] for k in (2, 10, 100)]
    assert all(d <= one["psr_vs_zero"] for d in dsrs)
    assert dsrs == sorted(dsrs, reverse=True)
    explicit = inf.sharpe_inference(rets, n_trials=10, trial_sharpe_variance=0.5)
    assert explicit["dsr"] <= one["psr_vs_zero"]


def test_min_track_record_shrinks_with_higher_sharpe():
    lengths = [inf.min_track_record(sr) for sr in (0.03, 0.06, 0.12)]
    assert lengths == sorted(lengths, reverse=True)
    assert inf.min_track_record(0.0) is None


def test_sharpe_inference_floor():
    assert inf.sharpe_inference(_returns(0.001, n=59)) is None
    assert inf.sharpe_inference([0.001] * 100) is None
