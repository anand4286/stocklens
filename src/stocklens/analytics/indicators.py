"""Technical indicators as pure functions over closing prices.

Each returns `None` when there is not enough history, never a number computed
from a partial window: an RSI over 6 bars is not a weak RSI, it is not an RSI.
Definitions follow Wilder (RSI, ATR) and Appel (MACD) as published.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def sma_series(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    """EMA seeded with the SMA of the first `period` values."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = values[i] * k + current * (1 - k)
        out[i] = current
    return out


def rsi_series(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI with his smoothing (an EMA with alpha = 1/period)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / period, losses / period

    def value() -> float:
        if avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    out[period] = value()
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        out[i] = value()
    return out


def macd_series(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """(macd, signal, histogram) series, aligned to `closes`."""
    fast_ema, slow_ema = ema_series(closes, fast), ema_series(closes, slow)
    line = [f - s if f is not None and s is not None else None
            for f, s in zip(fast_ema, slow_ema, strict=True)]
    first = next((i for i, v in enumerate(line) if v is not None), None)
    sig: list[float | None] = [None] * len(closes)
    if first is not None:
        tail = ema_series([v for v in line[first:] if v is not None], signal)
        sig[first:] = tail
    hist = [m - s if m is not None and s is not None else None
            for m, s in zip(line, sig, strict=True)]
    return line, sig, hist


def bollinger(closes: Sequence[float], period: int = 20, width: float = 2.0):
    """(lower, middle, upper) for the latest bar, population standard deviation."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    sd = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    return mid - width * sd, mid, mid + width * sd


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def stochastic_k(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                 period: int = 14) -> float | None:
    if len(closes) < period:
        return None
    hi, lo = max(highs[-period:]), min(lows[-period:])
    if hi == lo:
        return 50.0
    return 100 * (closes[-1] - lo) / (hi - lo)


def pct_change(values: Sequence[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return values[-1] / values[-periods - 1] - 1
