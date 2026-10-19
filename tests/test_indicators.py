import math

import pytest

from stocklens.analytics import indicators as ind


def test_sma_needs_a_full_window():
    assert ind.sma([1, 2, 3], 4) is None
    assert ind.sma([1, 2, 3, 4], 4) == 2.5


def test_sma_series_matches_pointwise_sma():
    values = [float(x) for x in range(1, 30)]
    series = ind.sma_series(values, 5)
    assert series[:4] == [None] * 4
    for i in range(4, len(values)):
        assert series[i] == pytest.approx(ind.sma(values[: i + 1], 5))


def test_rsi_is_100_when_price_only_rises_and_0_when_it_only_falls():
    rising = [float(x) for x in range(1, 40)]
    assert ind.rsi_series(rising)[-1] == 100.0
    falling = list(reversed(rising))
    assert ind.rsi_series(falling)[-1] == pytest.approx(0.0)


def test_rsi_is_bounded_and_absent_before_its_window():
    closes = [100 + 5 * math.sin(i / 3) for i in range(80)]
    rsi = ind.rsi_series(closes)
    assert rsi[:14] == [None] * 14
    assert all(0 <= v <= 100 for v in rsi[14:])


def test_macd_histogram_is_line_minus_signal():
    closes = [100 + i * 0.3 + math.sin(i) for i in range(120)]
    line, signal, hist = ind.macd_series(closes)
    assert len(line) == len(signal) == len(hist) == len(closes)
    for m, s, h in zip(line, signal, hist, strict=True):
        if h is not None:
            assert h == pytest.approx(m - s)


def test_bollinger_band_is_symmetric_around_the_mean():
    lo, mid, hi = ind.bollinger([float(x) for x in range(1, 21)])
    assert mid == pytest.approx(10.5)
    assert hi - mid == pytest.approx(mid - lo)


def test_atr_of_a_constant_range_is_that_range():
    n = 40
    highs, lows, closes = [11.0] * n, [9.0] * n, [10.0] * n
    assert ind.atr(highs, lows, closes) == pytest.approx(2.0)
