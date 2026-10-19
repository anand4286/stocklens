"""Has this stock traced this shape before, and what followed? Said honestly.

What this is
------------
Shape matching applied to one symbol's *own* history: take the last `window`
sessions of log price, z-normalise it, slide the same-length window across
everything earlier, and keep the `k` closest past paths by Euclidean distance.
For each, report what the stock did over the next `horizon` sessions.

Why it needs a warning attached to the number
---------------------------------------------
Ten years of daily bars hold thousands of candidate windows. Something will
always resemble anything, closely, by construction: the closest match to any
30-day path is a near-certainty rather than a finding. **A match on its own is
not evidence of anything.**

What can be evidence is the *distribution of what happened next* across many
matches, **compared with the base rate**: every `horizon`-session return in the
same history. If the ten closest analogues rose 60% of the time and every window
rose 58% of the time, the resemblance carried no information, and that is the
common outcome. So the base rate is always returned beside the analogue outcome,
together with the standard error that says whether the gap is bigger than the
noise of averaging ten numbers.

Why z-normalised log price
--------------------------
A pattern is a shape, not a price. Each window is centred and scaled by its own
standard deviation, so a run from $40 to $44 and one from $400 to $440 compare
as the same form. Log price first, so a 10% move is the same shape at any level.
A window with no variation is dropped rather than divided by zero: a stock that
did not move has no shape to match.

Why an exclusion zone
---------------------
Overlapping windows are the same episode wearing several hats. Without an
exclusion zone the "ten closest analogues" are routinely ten consecutive days of
one episode, and their forward returns are the same number reported ten times.
Every match is therefore at least `window` sessions from the query and from
every other match taken. With `horizon <= window` (the default, 21 <= 30) the
forward periods of different matches cannot overlap either; a longer horizon
lets them share sessions, and the standard error then flatters the sample.

Why Euclidean and not dynamic time warping
------------------------------------------
DTW would align a three-day drop with a three-week one. Those are different
events with different consequences; rigid alignment keeps the time axis honest.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from stocklens.data.base import Bar

#: Windows shorter than this describe noise rather than a pattern.
MIN_WINDOW = 10

#: Below this many analogues with an outcome, the spread of what followed says
#: more about which windows happened to be found than about the pattern.
MIN_ANALOGS = 10

#: How many standard errors the analogue mean must clear the base rate by before
#: the gap is called significant. Two, roughly a 95% two-sided test, and still
#: generous given the multiple-comparisons problem in `WARNING`.
SIGNIFICANCE_SE = 2.0

WARNING = (
    "Shape matching searches every past window and keeps the closest, so a close match "
    "is guaranteed by construction and is not evidence. The analogues are compared with "
    "the base rate of all same-horizon returns in this history; even a gap of two standard "
    "errors is expected by chance in about one search in twenty, and many symbols and "
    "window lengths are searched. Descriptive only, not a forecast."
)


def znormalise(window: Sequence[float]) -> list[float] | None:
    """Centre and scale a window so the comparison is about form, not price."""
    if len(window) < 2:
        return None
    mean = sum(window) / len(window)
    deviation = math.sqrt(sum((v - mean) ** 2 for v in window) / len(window))
    if deviation <= 1e-12 * max(1.0, abs(mean)):
        return None  # no variation: no shape to match
    return [(v - mean) / deviation for v in window]


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _stdev(xs: Sequence[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def base_rate(closes: Sequence[float], horizon: int) -> dict:
    """Every `horizon`-session simple return in the history, overlapping.

    Overlap does not bias the mean or the share up; it only makes the
    observations dependent, which is why no standard error is quoted for it.
    """
    rets = [closes[i + horizon] / closes[i] - 1 for i in range(len(closes) - horizon)]
    if not rets:
        return {"n": 0, "mean_forward": None, "share_up": None, "stdev": None}
    return {
        "n": len(rets),
        "mean_forward": sum(rets) / len(rets),
        "share_up": sum(1 for r in rets if r > 0) / len(rets),
        "stdev": _stdev(rets) if len(rets) >= 2 else None,
    }


def _verdict(n: int, mean: float, share_up: float, base: dict, edge: float | None,
             stderr: float | None, significant: bool, horizon: int) -> str:
    if n < MIN_ANALOGS:
        return (
            f"Only {n} analogues have an outcome. Below {MIN_ANALOGS}, the spread of what "
            "followed says more about which windows happened to be found than about the shape."
        )
    if edge is None or stderr is None:
        return f"The {n} closest analogues averaged {mean:+.2%} with no base rate to compare."
    head = (
        f"The {n} closest analogues returned {mean:+.2%} on average over the next {horizon} "
        f"sessions and rose {share_up:.0%} of the time. Every {horizon}-session window in this "
        f"history averaged {base['mean_forward']:+.2%} and rose {base['share_up']:.0%}."
    )
    if not significant:
        return (
            f"{head} The gap of {edge:+.2%} is within {SIGNIFICANCE_SE:g} standard errors "
            f"(SE {stderr:.2%}), so the resemblance carried no information. That is the usual "
            "outcome, and the reason this comparison is always shown."
        )
    direction = "above" if edge > 0 else "below"
    return (
        f"{head} The analogues sit {abs(edge):.2%} {direction} the base rate, more than "
        f"{SIGNIFICANCE_SE:g} standard errors (SE {stderr:.2%}). Worth noting, not worth "
        "acting on alone: the sample is small, the windows share one stock's regime, and "
        "the search itself manufactures close matches."
    )


def analogs(bars: list[Bar], window: int = 30, horizon: int = 21, k: int = 10) -> dict | None:
    """The `k` closest past shapes to the latest `window` sessions, and what followed.

    None when the history cannot supply at least `k` non-overlapping candidates
    with a complete forward return, or when the latest window is flat.
    """
    if window < MIN_WINDOW or horizon < 1 or k < 1:
        return None
    usable = [b for b in bars if b.close > 0 and math.isfinite(b.close)]
    closes = [b.close for b in usable]
    logs = [math.log(c) for c in closes]
    n = len(closes)
    q_start = n - window
    # A candidate starting at s must (a) keep `window` clear of the query and
    # (b) have its forward return already observed: s + window - 1 + horizon <= n - 1.
    last_start = min(q_start - window, n - window - horizon)
    if q_start < 0 or last_start + 1 < k * window:
        return None  # not enough room for k non-overlapping analogues
    query = znormalise(logs[q_start:])
    if query is None:
        return None

    scored: list[tuple[float, int, list[float]]] = []
    for s in range(0, last_start + 1):
        cand = znormalise(logs[s:s + window])
        if cand is not None:
            scored.append((distance(query, cand), s, cand))
    scored.sort(key=lambda t: (t[0], t[1]))

    taken: list[tuple[float, int, list[float]]] = []
    for item in scored:
        if all(abs(item[1] - other[1]) >= window for other in taken):
            taken.append(item)
            if len(taken) >= k:
                break

    matches = []
    forwards = []
    for dist, s, path in taken:
        end = s + window - 1
        fwd = closes[end + horizon] / closes[end] - 1
        forwards.append(fwd)
        matches.append({
            "start": usable[s].day.isoformat(),
            "end": usable[end].day.isoformat(),
            "distance": round(dist, 4),
            "forward_return": round(fwd, 4),
            "path": [round(v, 3) for v in path],
        })

    m = len(forwards)
    mean = sum(forwards) / m if m else 0.0
    share_up = sum(1 for f in forwards if f > 0) / m if m else 0.0
    base = base_rate(closes, horizon)
    # Under the null the analogues are draws from the base distribution, so its
    # spread is the yardstick. Ten matches' own spread is itself too noisy: with
    # it, demo random walks cleared two standard errors 9% of the time, not 5%.
    stderr = base["stdev"] / math.sqrt(m) if m >= 2 and base["stdev"] else None
    edge = mean - base["mean_forward"] if m and base["mean_forward"] is not None else None
    significant = bool(
        m >= MIN_ANALOGS and edge is not None and stderr is not None
        and abs(edge) > SIGNIFICANCE_SE * stderr
    )
    return {
        "window": window,
        "horizon": horizon,
        "candidates": len(scored),
        "query": {
            "start": usable[q_start].day.isoformat(),
            "end": usable[-1].day.isoformat(),
            "path": [round(v, 3) for v in query],
        },
        "matches": matches,
        "summary": {
            "n": m,
            "mean_forward": round(mean, 4) if m else None,
            "median_forward": round(_median(forwards), 4) if m else None,
            "share_up": round(share_up, 4) if m else None,
            "stderr": round(stderr, 4) if stderr is not None else None,
        },
        "base_rate": {
            "n": base["n"],
            "mean_forward": (round(base["mean_forward"], 4)
                             if base["mean_forward"] is not None else None),
            "share_up": round(base["share_up"], 4) if base["share_up"] is not None else None,
        },
        "edge": round(edge, 4) if edge is not None else None,
        "significant": significant,
        "verdict": (_verdict(m, mean, share_up, base, edge, stderr, significant, horizon)
                    if m else "No analogue has a measurable outcome."),
        "warning": WARNING,
    }
