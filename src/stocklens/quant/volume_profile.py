"""Where volume actually traded, by price rather than by time.

Why this exists
---------------
Every indicator in `analytics/indicators.py` is a function of *time*: RSI over
fourteen sessions, a moving average over fifty. None of them is a function of
*price level*, so none can answer the question a reader actually asks of a
chart: **at what price did this stock change hands, and where did it not?**

That is the supply-and-demand structure. A price where a great deal of volume
traded is a price both sides accepted; a price where almost none did is one the
market moved through and refused. Support and resistance drawn this way are
earned from the tape rather than drawn by eye.

The vocabulary is Market Profile's, and is forty years old:

- **Point of control (POC)**: the price band with the most volume. Fair value
  by revealed preference rather than by opinion.
- **Value area**: the contiguous band around the POC holding 70% of the volume.
  Where the market agreed.
- **High volume node (HVN)**: a local peak. Price kept returning here.
- **Low volume node (LVN)**: a local trough. Price passed through quickly.

What this is not
----------------
It is not a forecast; StockLens is a read-only analytics site and nothing here
trades. `respects_the_levels` measures, out of sample, whether price behaves
differently at these levels than at arbitrary ones. Until that measurement says
yes, the profile is a description of the past and is labelled as one.

The honest limits of the input
------------------------------
A true profile is built from ticks. StockLens has daily bars, so the volume
inside one session is known in total but not by price. It is spread
**uniformly across that bar's high-low range**, each bin receiving volume in
proportion to how much of the range it overlaps. On a daily bar that is a guess
across a whole session: a day that opened at the high, sold off and spent six
hours pinned at the low is drawn as if it traded evenly all the way down. The
profile should be read as indicative, and `BASIS` says so wherever it is shown.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from stocklens.data.base import Bar

#: Price bins across the observed range. The width falls out of the symbol's
#: own range divided by this, so a $5 stock and a $500 stock get bins that mean
#: the same thing relative to how far each moved. Forty rather than the usual fifty
#: because a year of daily bars is ~250 observations, not tens of thousands of
#: minutes: fewer, fuller bins are a reading rather than a sample of one.
BINS = 40

#: Below this the profile is withheld. A shape drawn from a handful of sessions
#: is not a weak profile, it is not a profile.
MIN_BARS = 60

#: Share of volume the value area encloses. The Market Profile convention,
#: borrowed rather than derived (one normal standard deviation applied to a
#: distribution that is not normal), and stated wherever the band is shown.
VALUE_AREA_SHARE = 0.70

#: A local peak (or trough) must stand out from the shoulders around it by at
#: least this share of the POC's volume to count as a node. Without a floor
#: every one-bin wiggle in a noisy histogram is a "level"; with it, a node is a
#: shelf a reader could see on the chart.
MIN_PROMINENCE = 0.10

#: Out-of-sample split for the level test.
TRAIN_SHARE = 0.70

#: How close price must come to a level to count as a test, in bin widths.
TOUCH_TOLERANCE = 0.5

#: Sessions after a touch over which the outcome is judged. A week: the claim is
#: that a level *holds*, and a reversal a month later is a different market.
OUTCOME_BARS = 5

#: Touches below which a rate is not reported. Missing is not zero.
MIN_TOUCHES = 15

#: Smallest train and test windows the level test will split into.
MIN_TRAIN = 40
MIN_TEST = 20

#: Evenly spaced control prices per real level in the base-rate comparison.
CONTROLS = 12

BASIS = (
    "Built from daily bars, not ticks: each session's volume is spread evenly across "
    "its high-low range, so the profile is indicative only."
)


def _r(x: float, nd: int) -> float:
    return round(x, nd)


def _usable(bars: Sequence[Bar]) -> list[Bar]:
    return [
        b for b in bars
        if b.volume > 0 and b.high >= b.low > 0 and math.isfinite(b.volume + b.high + b.low)
    ]


def _histogram(bars: Sequence[Bar], low: float, width: float, bins: int) -> list[float]:
    """Spread each bar's volume over the bins its range overlaps, by overlap length."""
    buckets = [0.0] * bins
    for bar in bars:
        first = min(bins - 1, max(0, int((bar.low - low) / width)))
        last = min(bins - 1, max(0, int((bar.high - low) / width)))
        span = bar.high - bar.low
        if span <= 0 or first == last:
            # A doji or a bar inside one bin: all of it lands in that bin.
            buckets[first] += bar.volume
            continue
        placed = 0.0
        for i in range(first, last + 1):
            lo = max(bar.low, low + i * width)
            hi = min(bar.high, low + (i + 1) * width)
            piece = bar.volume * max(0.0, hi - lo) / span
            buckets[i] += piece
            placed += piece
        # Float slack from the edge clamps goes to the last bin, so volume is conserved.
        buckets[last] += bar.volume - placed
    return buckets


def _value_area(buckets: Sequence[float], peak: int) -> tuple[int, int]:
    """Grow outward from the POC, always taking the busier neighbour.

    Contiguity matters: the value area is the band the market agreed on, so a
    distant busy shelf does not join it by being busy. Taking the larger
    neighbour each step keeps the band centred on where trade concentrated.
    """
    target = sum(buckets) * VALUE_AREA_SHARE
    lower = upper = peak
    held = buckets[peak]
    last = len(buckets) - 1
    while held < target and (lower > 0 or upper < last):
        below = buckets[lower - 1] if lower > 0 else -1.0
        above = buckets[upper + 1] if upper < last else -1.0
        if above >= below:
            upper += 1
            held += buckets[upper]
        else:
            lower -= 1
            held += buckets[lower]
    return lower, upper


def _nodes(buckets: Sequence[float], floor: float) -> tuple[list[int], list[int]]:
    """Local peaks and troughs whose topographic prominence clears `floor`.

    A peak's prominence is how far it rises above the higher of the two lowest
    points between it and the next taller bin on each side (or the edge). A
    trough's is the mirror image. Edge bins can be peaks but not troughs: the
    tails of any histogram thin out, and that is not a level.
    """
    n = len(buckets)
    peaks: list[int] = []
    troughs: list[int] = []
    for i, v in enumerate(buckets):
        left = buckets[i - 1] if i > 0 else None
        right = buckets[i + 1] if i < n - 1 else None
        # >= on the left, > on the right: a plateau is counted once, at its right end.
        if (left is None or v >= left) and (right is None or v > right):
            towards_taller, run_lows = [], []
            for step in (-1, 1):
                j, lowest = i + step, v
                while 0 <= j < n and buckets[j] <= v:
                    lowest = min(lowest, buckets[j])
                    j += step
                run_lows.append(lowest)
                if 0 <= j < n:  # stopped at a taller bin: the col on this side counts
                    towards_taller.append(lowest)
            # The tallest peak has no taller bin either side; its prominence is
            # measured down to the lowest point of the histogram.
            col = max(towards_taller) if towards_taller else min(run_lows)
            if v - col >= floor:
                peaks.append(i)
        if left is not None and right is not None and v <= left and v < right:
            rims = []
            for step in (-1, 1):
                j, highest = i + step, v
                while 0 <= j < n and buckets[j] >= v:
                    highest = max(highest, buckets[j])
                    j += step
                rims.append(highest)
            if min(rims) - v >= floor:
                troughs.append(i)
    return peaks, troughs


def _profile(bars: Sequence[Bar], bins: int) -> dict | None:
    """The raw shape, unrounded: shared by `build` and the level test."""
    low = min(b.low for b in bars)
    high = max(b.high for b in bars)
    if high <= low:
        return None  # the price never moved: there is no profile to draw
    width = (high - low) / bins
    buckets = _histogram(bars, low, width, bins)
    total = sum(buckets)
    if total <= 0:
        return None
    peak = max(range(bins), key=lambda i: buckets[i])
    lower, upper = _value_area(buckets, peak)
    hvn, lvn = _nodes(buckets, buckets[peak] * MIN_PROMINENCE)
    mids = [low + width * (i + 0.5) for i in range(bins)]
    return {
        "low": low, "width": width, "buckets": buckets, "total": total, "peak": peak,
        "lower": lower, "upper": upper, "hvn": hvn, "lvn": lvn, "mids": mids,
    }


def _score(bars: Sequence[Bar], levels: Sequence[float], tolerance: float) -> tuple[int, int]:
    """Bars that touched any level, and how many of those the level held.

    Held means: price came within tolerance of the level and, `OUTCOME_BARS`
    later, closes on the side it approached from. The weakest claim a level can
    make and still be useful. One touch per bar, at the nearest level, so a bar
    spanning three levels is one observation and not three.
    """
    touches = reactions = 0
    for i, bar in enumerate(bars):
        if i + OUTCOME_BARS >= len(bars):
            break
        hit = [lv for lv in levels if bar.low - tolerance <= lv <= bar.high + tolerance]
        if not hit:
            continue
        before = bars[i - 1].close if i else bar.open
        level = min(hit, key=lambda lv: abs(lv - before))
        if before == level:
            continue  # started on the level: no side to hold
        after = bars[i + OUTCOME_BARS].close
        touches += 1
        reactions += (after > level) if before > level else (after < level)
    return touches, reactions


def respects_the_levels(bars: Sequence[Bar], bins: int = BINS) -> dict | None:
    """Does price react at these levels more than at arbitrary ones, out of sample?

    Built to be hard to pass:

    - **Out of sample.** Levels (POC and HVNs) come from the first 70% of the
      bars and are tested on the last 30%. Testing on the bars that drew the
      profile is circular: an HVN is *defined* as where trade happened.
    - **Against a base rate.** Control prices are spaced evenly across the range
      the *test* window actually traded, skipping anything near a real level.
      Mirroring levels around the value area, the obvious first design, puts
      controls where price never went; two groups sampled from different price
      regions cannot be compared. Evenly spaced controls are deterministic, so
      the same history always gives the same answer (no RNG).

    Returns None when the window is too short to split.
    """
    usable = _usable(bars)
    split = int(len(usable) * TRAIN_SHARE)
    train, test = usable[:split], usable[split:]
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        return None
    shape = _profile(train, bins)
    if shape is None:
        return None
    levels = [shape["mids"][i] for i in sorted({shape["peak"], *shape["hvn"]})]
    tolerance = shape["width"] * TOUCH_TOLERANCE

    span_low = min(b.low for b in test)
    span_high = max(b.high for b in test)
    steps = max(CONTROLS, len(levels) * 3)
    grid = [span_low + (span_high - span_low) * s / (steps - 1) for s in range(steps)]
    controls = [c for c in grid if all(abs(c - lv) > 2 * tolerance for lv in levels)]

    touches, reactions = _score(test, levels, tolerance)
    c_touches, c_reactions = _score(test, controls, tolerance)
    rate = reactions / touches if touches >= MIN_TOUCHES else None
    base = c_reactions / c_touches if c_touches >= MIN_TOUCHES else None
    lift = rate / base if rate is not None and base else None

    if rate is None or base is None:
        verdict = (
            f"{touches} touches of the levels and {c_touches} of the controls in the "
            f"{len(test)}-session test window, against the {MIN_TOUCHES} a rate needs: "
            "not a result either way."
        )
    else:
        se = math.sqrt(rate * (1 - rate) / touches + base * (1 - base) / c_touches)
        diff = rate - base
        if se > 0 and abs(diff) > 2 * se:
            word = "more" if diff > 0 else "less"
            verdict = (
                f"Out of sample, price held at these levels {rate:.0%} of {touches} touches "
                f"against {base:.0%} at arbitrary prices: {word} often by more than two "
                "standard errors. Evidence the levels mattered in this window, not a forecast."
            )
        else:
            verdict = (
                f"Out of sample, price held at these levels {rate:.0%} of {touches} touches "
                f"against {base:.0%} at arbitrary prices. The difference is inside two "
                "standard errors, so the levels did not behave differently from nothing in "
                "particular. Read the profile as a description of the past."
            )
    return {
        "touches": touches,
        "reactions": reactions,
        "reaction_rate": _r(rate, 4) if rate is not None else None,
        "base_rate": _r(base, 4) if base is not None else None,
        "lift": _r(lift, 3) if lift is not None else None,
        "verdict": verdict,
    }


def build(bars: list[Bar], lookback: int = 252, bins: int = BINS) -> dict | None:
    """Volume by price over the last `lookback` sessions. None below `MIN_BARS`."""
    if bins < 3:
        raise ValueError("bins must be at least 3")
    window = _usable(bars[-lookback:] if lookback > 0 else bars)
    if len(window) < MIN_BARS:
        return None
    shape = _profile(window, bins)
    if shape is None:
        return None

    low, width, buckets, total = shape["low"], shape["width"], shape["buckets"], shape["total"]
    lower, upper, mids = shape["lower"], shape["upper"], shape["mids"]
    out_bins = [
        {
            "low": _r(low + width * i, 4),
            "high": _r(low + width * (i + 1), 4),
            "volume": _r(v, 2),
            "share": _r(v / total, 4),
            "in_value_area": lower <= i <= upper,
        }
        for i, v in enumerate(buckets)
    ]
    poc = mids[shape["peak"]]
    va_low = low + width * lower
    va_high = low + width * (upper + 1)
    price = window[-1].close
    if price > va_high:
        position = "above_value"
    elif price < va_low:
        position = "below_value"
    else:
        position = "inside_value"
    distance = price / poc - 1

    if position == "above_value":
        where = "above the value area: the market has not yet agreed at this price"
    elif position == "below_value":
        where = "below the value area: a price the market has rarely accepted"
    else:
        where = "inside the value area, where most of the year's trade agreed"
    reading = (
        f"Over {len(window)} sessions, the most volume traded near {poc:.2f} and 70% of it "
        f"between {va_low:.2f} and {va_high:.2f}. The last close of {price:.2f} sits {where}, "
        f"{distance:+.1%} from the point of control. {BASIS}"
    )
    return {
        "lookback": len(window),
        "bin_width": _r(width, 4),
        "bins": out_bins,
        "poc": _r(poc, 4),
        "value_area_low": _r(va_low, 4),
        "value_area_high": _r(va_high, 4),
        "value_area_share": VALUE_AREA_SHARE,
        "hvn": [_r(mids[i], 4) for i in shape["hvn"]],
        "lvn": [_r(mids[i], 4) for i in shape["lvn"]],
        "price": _r(price, 4),
        "position": position,
        "distance_to_poc_pct": _r(distance, 4),
        "level_test": respects_the_levels(window, bins),
        "reading": reading,
    }
