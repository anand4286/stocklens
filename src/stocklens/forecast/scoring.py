"""Grading a probabilistic record: is it skilful, is it honest, and how sure can we be?

Why not just accuracy
---------------------
A forecaster that says "up, 51%" and is right 51% of the time is perfectly
honest and useless. One that says "up, 90%" and is right 60% of the time is
dangerous: anything that sizes on its probabilities bets too large and loses
money while being right more often than not. Accuracy cannot tell those two
apart. These can:

* **Brier score** — mean squared error of the probability against the 0/1
  outcome. A *proper* scoring rule: no forecaster can improve its expected
  score by reporting anything other than what it believes, so it cannot be
  gamed by overclaiming. Always saying 50% scores 0.25.
* **Brier skill score** — ``1 − Brier / Brier_climatology``, where climatology
  is the constant forecast equal to the base rate of up moves *in this same
  sample*. Zero means no better than knowing the base rate; negative means
  worse. This is the headline, because a rising market makes "up" right most
  of the time and a raw hit rate takes credit for the market's drift.
  Climatology is measured in-sample, which hands it hindsight the forecaster
  did not have — so a BSS a little below zero is what no skill looks like.
* **Log loss** — punishes confident misses far harder than Brier does. Clipped
  at 1e-6 so a single 0/1 forecast cannot make it infinite.
* **Reliability** — forecasts binned by stated probability. In each bin the
  observed frequency of up moves should match the mean prediction, within a
  Wilson interval. This is what grades the shrunk map in `model`.
* **Range coverage** — how often the outcome landed inside the one-sigma
  range. Nominal is 68.3%. Far below means the volatility estimate is too
  low; far above, too high.

How many observations there really are
--------------------------------------
Rows are not independent. Every symbol scored on one day shares that day's
market, and one factor dominates equity returns, so twenty forecasts made on a
Monday are much closer to one observation of that Monday than to twenty of
anything. So the effective sample is counted in
**distinct decision days** (`made_on`, the day the features were observed —
never the day the resolver happened to run), and the intervals on the hit
rate are computed at that size. That is still generous: forecasts with a
21-session horizon made a week apart overlap in most of their window. The
stricter count, non-overlapping horizon windows, is reported beside it.

Slices
------
The record is also cut by regime and by confidence. A
model can be skilful in trending markets and useless otherwise, and if
high-confidence calls are no more accurate than low-confidence ones then the
confidence itself is broken whatever the headline says. Per-family
attribution asks how often each family's sign agreed with the outcome. Every
slice carries its own count, and one below `MIN_SLICE` is flagged rather
than hidden: 80% right on six forecasts is not a finding.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from ..quant.inference import hoeffding_interval, wilson_interval
from .model import HORIZON

#: A slice below this many forecasts is reported but flagged unreliable. At
#: n = 30 a Wilson interval on a coin is still about ±17 points wide.
MIN_SLICE = 30

#: Below these the verdict is "not enough evidence", whatever the numbers say.
MIN_RESOLVED = 30
MIN_DECISION_DAYS = 20

#: Reliability bin edges on p_up. The map in `model` keeps p_up inside about
#: [0.33, 0.67], so the outer bins are open-ended rather than empty.
BIN_EDGES = (0.45, 0.50, 0.55)
BIN_LABELS = ("< 0.45", "0.45 – 0.50", "0.50 – 0.55", "≥ 0.55")

#: Confidence buckets on |p_up − 0.5|.
CONFIDENCE_BUCKETS = (("low", 0.0, 0.05), ("medium", 0.05, 0.10), ("high", 0.10, 1.0))

#: Probability a normal variable lands within one standard deviation.
NOMINAL_COVERAGE = 0.6827

_EPS = 1e-6


def _r(x: float | None, places: int = 4) -> float | None:
    return None if x is None else round(x, places)


def _y(f: dict) -> int:
    return 1 if f["outcome_up"] else 0


def _correct(f: dict) -> bool:
    return (f["p_up"] >= 0.5) == bool(f["outcome_up"])


def brier(forecasts: list[dict]) -> float | None:
    if not forecasts:
        return None
    return sum((f["p_up"] - _y(f)) ** 2 for f in forecasts) / len(forecasts)


def log_loss(forecasts: list[dict]) -> float | None:
    if not forecasts:
        return None
    total = 0.0
    for f in forecasts:
        p = min(1 - _EPS, max(_EPS, f["p_up"]))
        total -= math.log(p) if f["outcome_up"] else math.log(1 - p)
    return total / len(forecasts)


def brier_skill(forecasts: list[dict]) -> float | None:
    """``1 − Brier / Brier(climatology)``; None when the sample's base rate is 0 or 1."""
    if not forecasts:
        return None
    base = sum(_y(f) for f in forecasts) / len(forecasts)
    reference = base * (1 - base)
    if reference <= 0:
        return None
    return 1 - brier(forecasts) / reference


def decision_days(forecasts: list[dict]) -> int:
    return len({f["made_on"] for f in forecasts})


def non_overlapping_windows(forecasts: list[dict], horizon: int = HORIZON) -> int:
    """Decision days kept greedily so no two are within `horizon` sessions (weekdays)."""
    days = sorted({date.fromisoformat(str(f["made_on"])) for f in forecasts})
    kept, last = 0, None
    for d in days:
        if last is None or _weekdays_between(last, d) >= horizon:
            kept += 1
            last = d
    return kept


#: Below this many independent blocks a t-statistic has too few degrees of
#: freedom to mean anything, and the block test abstains.
MIN_BLOCKS = 8
SKILL_T = 2.0


def block_skill_test(forecasts: list[dict], horizon: int = HORIZON) -> dict:
    """Is the Brier improvement over climatology larger than its own noise?

    Two dependencies make a row count or a day count overstate the evidence.
    Every symbol on one day shares the market's next 21 sessions, so same-day
    rows are close to one observation; and forecasts five sessions apart with
    21-session outcomes overlap in time. So: average the per-forecast Brier
    improvement within each day, average the days within consecutive
    `horizon`-session blocks, and run a t-test across the blocks — which are
    close to independent. A 5-symbol random-walk basket earned "skill" on the
    plain interval; this is the test that stops that.
    """
    rows = [f for f in forecasts if f.get("outcome_up") is not None]
    if not rows:
        return {"blocks": 0, "mean_improvement": None, "t_stat": None, "passed": False}
    base = sum(_y(f) for f in rows) / len(rows)
    by_day: dict[date, list[float]] = {}
    for f in rows:
        y = _y(f)
        gain = (base - y) ** 2 - (f["p_up"] - y) ** 2
        by_day.setdefault(date.fromisoformat(str(f["made_on"])), []).append(gain)
    days = sorted(by_day)
    blocks: list[list[float]] = []
    start = None
    for d in days:
        if start is None or _weekdays_between(start, d) >= horizon:
            blocks.append([])
            start = d
        blocks[-1].append(sum(by_day[d]) / len(by_day[d]))
    means = [sum(b) / len(b) for b in blocks]
    k = len(means)
    if k < MIN_BLOCKS:
        return {"blocks": k, "mean_improvement": None, "t_stat": None, "passed": False}
    m = sum(means) / k
    sd = math.sqrt(sum((x - m) ** 2 for x in means) / (k - 1))
    t = m / (sd / math.sqrt(k)) if sd > 0 else None
    return {"blocks": k, "mean_improvement": _r(m), "t_stat": _r(t),
            "passed": t is not None and t >= SKILL_T}


def _weekdays_between(a: date, b: date) -> int:
    whole, rest = divmod((b - a).days, 7)
    count = whole * 5
    tail = a + timedelta(days=whole * 7)
    return count + sum((tail + timedelta(days=i)).weekday() < 5 for i in range(1, rest + 1))


def _effective_bounds(successes: int, n: int, n_eff: int) -> dict:
    """Wilson and Hoeffding intervals with the rate held fixed and n shrunk to `n_eff`."""
    if not n or not n_eff:
        return {"wilson_95": None, "hoeffding_95": None, "samples": n_eff}
    k = round(successes / n * n_eff)
    w, h = wilson_interval(k, n_eff), hoeffding_interval(k, n_eff)
    return {"wilson_95": w.as_dict() if w else None,
            "hoeffding_95": h.as_dict() if h else None, "samples": n_eff}


def reliability(forecasts: list[dict]) -> list[dict]:
    bins: list[list[dict]] = [[] for _ in BIN_LABELS]
    for f in forecasts:
        i = sum(f["p_up"] >= edge for edge in BIN_EDGES)
        bins[i].append(f)
    table = []
    for label, members in zip(BIN_LABELS, bins, strict=True):
        n = len(members)
        ups = sum(_y(f) for f in members)
        w = wilson_interval(ups, n)
        mean_p = sum(f["p_up"] for f in members) / n if n else None
        table.append({
            "bin": label,
            "n": n,
            "decision_days": decision_days(members),
            "mean_predicted": _r(mean_p),
            "observed_up": _r(ups / n) if n else None,
            "wilson_95": w.as_dict() if w else None,
            "consistent": None if not w or mean_p is None else w.low <= mean_p <= w.high,
            "reliable": n >= MIN_SLICE,
        })
    return table


def _slice(label: str, members: list[dict]) -> dict:
    n = len(members)
    hits = sum(_correct(f) for f in members)
    w = wilson_interval(hits, n)
    return {
        "label": label,
        "n": n,
        "decision_days": decision_days(members),
        "hit_rate": _r(hits / n) if n else None,
        "wilson_95": w.as_dict() if w else None,
        "brier": _r(brier(members)),
        "brier_skill": _r(brier_skill(members)),
        "mean_p_up": _r(sum(f["p_up"] for f in members) / n) if n else None,
        "observed_up": _r(sum(_y(f) for f in members) / n) if n else None,
        "reliable": n >= MIN_SLICE,
    }


def by_regime(forecasts: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in forecasts:
        groups[f.get("regime") or "unknown"].append(f)
    return [_slice(k, v) for k, v in sorted(groups.items())]


def by_confidence(forecasts: list[dict]) -> list[dict]:
    out = []
    for label, lo, hi in CONFIDENCE_BUCKETS:
        members = [f for f in forecasts if lo <= abs(f["p_up"] - 0.5) < hi]
        out.append(_slice(label, members))
    return out


def by_family(forecasts: list[dict]) -> list[dict]:
    """How often each family's sign agreed with the outcome (per-family attribution).

    A family scoring exactly zero argued nothing and is counted as used but
    not as a call. Agreement is against a coin, so read it with its interval.
    """
    names = sorted({k for f in forecasts for k in (f.get("features") or {})})
    out = []
    for name in names:
        used = calls = agree = 0
        for f in forecasts:
            s = (f.get("features") or {}).get(name)
            if s is None:
                continue
            used += 1
            if s == 0:
                continue
            calls += 1
            agree += (s > 0) == bool(f["outcome_up"])
        w = wilson_interval(agree, calls)
        out.append({"family": name, "used": used, "calls": calls,
                    "agreement": _r(agree / calls) if calls else None,
                    "wilson_95": w.as_dict() if w else None, "reliable": calls >= MIN_SLICE})
    return out


def _verdict(n: int, days: int, bss: float | None, hit_bound: dict | None,
             coverage: float | None, block: dict | None = None) -> dict:
    if n < MIN_RESOLVED or days < MIN_DECISION_DAYS:
        return {"label": "insufficient",
                "text": (f"{n} resolved forecasts over {days} decision days. Below "
                         f"{MIN_RESOLVED} forecasts and {MIN_DECISION_DAYS} days no claim "
                         "about skill is made either way.")}
    lo = hit_bound["low"] if hit_bound else None
    hi = hit_bound["high"] if hit_bound else None
    cov = "" if coverage is None else (
        f" The one-sigma range held {coverage:.0%} of outcomes against a nominal 68%.")
    block_ok = block is None or block["passed"]
    if bss is not None and bss > 0 and lo is not None and lo > 0.5 and block_ok:
        t_txt = "" if block is None else (
            f" and the improvement holds across {block['blocks']} independent "
            f"{HORIZON}-session blocks (t = {block['t_stat']:.2f})")
        return {"label": "skill",
                "text": (f"Brier skill {bss:+.3f} beats the base rate, the hit-rate interval "
                         f"[{lo:.0%}, {hi:.0%}] excludes a coin flip{t_txt}, on {days} "
                         "decision days." + cov)}
    if bss is not None and bss > 0 and lo is not None and lo > 0.5:
        t = block.get("t_stat") if block else None
        return {"label": "no_skill",
                "text": (f"Brier skill {bss:+.3f} and a hit-rate interval above 50% look like "
                         f"skill, but across {block['blocks']} independent {HORIZON}-session "
                         f"blocks the improvement has t = {'n/a' if t is None else f'{t:.2f}'}, "
                         f"below {SKILL_T:g}. Same-day forecasts share one market, so this is "
                         "most likely a few good months, not a model." + cov)}
    block_bad = block is None or (block.get("t_stat") is not None and block["t_stat"] <= -SKILL_T)
    if ((bss is not None and bss < -0.02) or (hi is not None and hi < 0.5)) and block_bad:
        return {"label": "worse_than_climatology",
                "text": (f"Brier skill {bss:+.3f}: the forecasts score worse than simply "
                         "quoting the base rate of up moves. The probabilities are adding "
                         "noise." + cov)}
    bss_txt = "n/a" if bss is None else f"{bss:+.3f}"
    if lo is not None and lo > 0.5:
        return {"label": "no_skill",
                "text": (f"The hit rate [{lo:.0%}, {hi:.0%}] is above 50%, but Brier skill is "
                         f"{bss_txt}: the stock mostly went one way, and simply quoting that "
                         "base rate scored as well as the forecasts did. A hit rate is only "
                         "skill when it beats the base rate, not a coin." + cov)}
    if hi is not None and hi < 0.5:
        return {"label": "no_skill",
                "text": (f"The hit rate [{lo:.0%}, {hi:.0%}] is below 50%, but across "
                         "independent blocks the shortfall is not significant either: no "
                         "reliable evidence of skill or of anti-skill." + cov)}
    return {"label": "no_skill",
            "text": (f"Brier skill {bss_txt} and a hit-rate interval "
                     f"[{lo:.0%}, {hi:.0%}] that contains 50%: on {days} decision days the "
                     "record cannot be told apart from knowing the base rate. That is the "
                     "honest default for a price-only model, and the expected answer on a "
                     "random walk." + cov)}


def score(resolved: list[dict]) -> dict:
    """Score resolved forecasts.

    Each needs `p_up`, `outcome_up` and `made_on`; `in_range`, `regime` and
    `features` are used when present. Unresolved rows (``outcome_up is None``)
    are ignored rather than counted as misses.
    """
    rows = [f for f in resolved if f.get("outcome_up") is not None]
    n = len(rows)
    days = decision_days(rows)
    windows = non_overlapping_windows(rows)
    hits = sum(_correct(f) for f in rows)
    ups = sum(_y(f) for f in rows)
    base = ups / n if n else None
    bss = brier_skill(rows)

    effective = _effective_bounds(hits, n, days)
    block = block_skill_test(rows)
    naive_w = wilson_interval(hits, n)

    ranged = [f for f in rows if f.get("in_range") is not None]
    inside = sum(1 for f in ranged if f["in_range"])
    cov_w = wilson_interval(inside, len(ranged))
    coverage = inside / len(ranged) if ranged else None

    return {
        "resolved": n,
        "decision_days": days,
        "non_overlapping_windows": windows,
        "horizon": HORIZON,
        "base_rate_up": _r(base),
        "brier": _r(brier(rows)),
        "brier_climatology": _r(base * (1 - base)) if base is not None else None,
        "brier_skill": _r(bss),
        "log_loss": _r(log_loss(rows)),
        "hit_rate": {
            "point": _r(hits / n) if n else None,
            "always_up": _r(base),
            "effective": effective,
            "naive_wilson_95": naive_w.as_dict() if naive_w else None,
            "note": ("Intervals under 'effective' are sized at the number of distinct decision "
                     "days, because same-day forecasts share one market. The naive interval "
                     "treats every row as independent and is shown only for contrast."),
        },
        "reliability": reliability(rows),
        "range_coverage": {
            "n": len(ranged),
            "inside": inside,
            "rate": _r(coverage),
            "nominal": NOMINAL_COVERAGE,
            "wilson_95": cov_w.as_dict() if cov_w else None,
        },
        "slices": {
            "regime": by_regime(rows),
            "confidence": by_confidence(rows),
            "family": by_family(rows),
        },
        "min_slice": MIN_SLICE,
        "block_test": block,
        "verdict": _verdict(n, days, bss, effective["wilson_95"], coverage, block),
    }
