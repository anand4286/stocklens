"""Walk-forward tests of every price family on one stock's own history.

The question
------------
The page says momentum is +0.6. Has momentum, *on this stock*, ever told you
anything about what happened next? An aggregator never asks; this module
does, and answers in a way designed to make "yes" hard to earn.

Point in time
-------------
The score on day t is computed from ``bars[: t + 1]`` and nothing else — in
practice from a trailing window of the last 300 of those bars, which keeps the
walk O(n·w) instead of O(n²) and is still strictly the past. The forward
return is ``close[t + h] / close[t] − 1``. The regime label on day t is
classified from ``closes[: t + 1]`` every five sessions and carried forward,
so it, too, only knows the past. Filings and news are not tested: the open
sources do not say what was knowable when, and a backtest that peeks is worse
than none.

Which families
--------------
Technical, momentum, relative strength (when a benchmark is supplied), and the
blended price composite with the published weights. Volume is a *conviction*
family — it says how much is happening, not which way — so it has no sign to
correlate with a return and is left out rather than forced into one.

The statistics, and what each one guards against
-----------------------------------------------
* **Rank IC, all days and non-overlapping days.** Consecutive 21-day forward
  returns share twenty days; counting them as independent inflates the sample
  twentyfold. The t-statistic is computed only on every h-th day.
* **Permutation p-value.** Shuffle the
  forward returns against the scores and ask how often chance alone produces a
  correlation this strong. It assumes nothing about the distribution of either.
* **Circular-shift p-value.** The permutation test assumes the forward returns
  are exchangeable, and they are not quite: a score built from past prices is
  persistent, and its innovations are the very returns it is then asked to
  predict (Stambaugh 1999). Measured on 40 independent Gaussian random walks,
  the permutation test rejects at nominal 5% about 7-9% of the time. Sliding
  the whole return series past the scores by every offset of at least h
  sessions keeps both series' autocorrelation intact and is closer to nominal
  (about 5-8%). Neither is exact, which is why the verdict needs both, plus a
  t-statistic, plus a deflated Sharpe: on those 40 walks, none passed.
* **Bands.** A real signal should be monotone: strong-up days should beat
  mild-up days should beat neutral. An IC driven by one tail is a story about
  a handful of days.
* **By year.** An edge that lived in one calendar year and reversed in the
  next is a regime, not a signal.
* **Rule economics.** A correlation is not money. A long/flat and a long/short
  rule are traded daily, paying ``cost_bps`` per unit of position change, and
  set against buy-and-hold over the same days — the honest benchmark for a
  stock that simply went up.
* **Deflated Sharpe.** Every (family × horizon × rule) combination tried here
  is a trial, and the best of sixteen zero-skill backtests has a positive
  Sharpe by selection alone. The Deflated Sharpe Ratio (Bailey & López de
  Prado 2014) raises the bar to the Sharpe that best-of-N would be expected to
  show. The multiple-testing charge is the point of the exercise.
* **Regime split.** IC inside trending, mean-reverting and random-walk spells.

The verdict
-----------
"Works here" needs all three of: non-overlapping t ≥ 2, a p-value — the larger
of the permutation and circular-shift tests — below the Bonferroni-corrected
alpha for every test run, and a deflated Sharpe above 0.95 on one of the rules.
"Evidence against" is the mirror image: t ≤ −2, the same p, and the long/short
rule traded *backwards* keeping a deflated Sharpe above 0.95. Anything less is
"no evidence", which is the right answer most of the time and the only answer
on a random walk. The bar is set high on purpose: two years of a persistent
score like 12-1 momentum hold only two or three independent readings, and a
lab that cried "works" on noise would be worse than the aggregator it replaces.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Sequence

from ..analytics import signals
from ..analytics.composite import DEFAULT_WEIGHTS
from ..data.base import Bar
from ..evaluate import _ranks, spearman
from ..quant import inference, regime

#: Below this many bars the walk is not attempted: a year of warm-up for 12-1
#: momentum leaves too few scored days to say anything.
MIN_BARS = 300
#: First scored session. 12-1 momentum needs 252 closes to be the measure the
#: page shows; scoring earlier would test a shorter, different signal.
WARMUP = 252
#: Trailing window handed to each signal function. Every family looks back at
#: most 252 sessions; 300 leaves room and keeps the walk linear in n.
WINDOW = 300
#: Regime is re-classified every this many sessions and carried forward.
REGIME_STEP = 5
#: Score above which the long/flat rule is long; also the neutral band's edge.
NEUTRAL = 0.15
STRONG = 0.5
#: Minimum permutation trials. Raised when the corrected alpha would otherwise
#: be finer than the test can resolve: with 500 trials p cannot fall below 1/501.
PERMUTATIONS = 500
SEED = 20260820
#: Below this many non-overlapping observations no t-statistic is quoted.
MIN_INDEPENDENT = 10
#: Below this many days a by-year or by-regime IC is not quoted.
MIN_SUBSAMPLE = 20
FAMILY_ALPHA = 0.05
DSR_BAR = 0.95
T_BAR = 2.0
MAX_CURVE_POINTS = 260
TRADING_DAYS = inference.TRADING_DAYS

LABELS = {
    "technical": "technical (RSI, trend, MACD)",
    "momentum": "12-1 month momentum",
    "relative_strength": "relative strength vs the benchmark",
    "price_composite": "the blended price composite",
}

BANDS = (
    ("strong_down", -math.inf, -STRONG),
    ("mild_down", -STRONG, -NEUTRAL),
    ("neutral", -NEUTRAL, NEUTRAL),
    ("mild_up", NEUTRAL, STRONG),
    ("strong_up", STRONG, math.inf),
)


# ------------------------------------------------------------------- scoring


def score_series(bars: list[Bar], bench: list[Bar] | None) -> dict[str, list[float | None]]:
    """Per-session scores for each family; entry t uses only ``bars[: t + 1]``.

    Entries before `WARMUP` are None. Relative strength is omitted without a
    benchmark; the benchmark is aligned by date, so a session only sees
    benchmark bars dated on or before it.
    """
    n = len(bars)
    names = ["technical", "momentum"] + (["relative_strength"] if bench else [])
    out: dict[str, list[float | None]] = {k: [None] * n for k in [*names, "price_composite"]}
    bench_days = [b.day for b in bench] if bench else []
    for t in range(WARMUP, n):
        window = bars[max(0, t + 1 - WINDOW): t + 1]
        fams = [signals.technical(window), signals.momentum(window)]
        if bench:
            k = bisect.bisect_right(bench_days, bars[t].day)
            fams.append(signals.relative_strength(window, bench[max(0, k - WINDOW): k]))
        used = []
        for f in fams:
            out[f.name][t] = f.score
            if f.available:
                used.append((DEFAULT_WEIGHTS[f.name], f.score))
        total = sum(w for w, _ in used)
        if total:
            out["price_composite"][t] = sum(w * s for w, s in used) / total
    return out


def regime_series(closes: list[float]) -> list[str | None]:
    """Trend regime per session from ``closes[: t + 1]``, refreshed every few sessions."""
    labels: list[str | None] = [None] * len(closes)
    current = None
    for t in range(WARMUP, len(closes)):
        if (t - WARMUP) % REGIME_STEP == 0:
            current = regime.classify(closes[: t + 1])["trend"]
        labels[t] = current
    return labels


def forward_returns(closes: Sequence[float], h: int) -> list[float | None]:
    return [closes[t + h] / closes[t] - 1 if t + h < len(closes) else None
            for t in range(len(closes))]


# -------------------------------------------------------------- statistics


def permutation_p(xs: Sequence[float], ys: Sequence[float], trials: int = PERMUTATIONS,
                  seed: int = SEED) -> float | None:
    """Two-sided permutation p for the rank correlation of `xs` and `ys`.

    Shuffles ranks rather than values: Spearman is Pearson on ranks, so the
    centred ranks and their norms are computed once and each trial is a dot
    product. ``(hits + 1) / (trials + 1)`` so p is never reported as zero.
    """
    if len(xs) < 3:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cx = [r - mx for r in rx]
    cy = [r - my for r in ry]
    den = math.sqrt(sum(v * v for v in cx) * sum(v * v for v in cy))
    if not den:
        return None
    observed = abs(sum(a * b for a, b in zip(cx, cy, strict=True))) - 1e-12 * den
    rng = random.Random(seed)  # noqa: S311  # nosec B311 - reproducibility, not secrecy
    hits = 0
    for _ in range(trials):
        rng.shuffle(cy)
        if abs(sum(a * b for a, b in zip(cx, cy, strict=True))) >= observed:
            hits += 1
    return (hits + 1) / (trials + 1)


def shift_p(xs: Sequence[float], ys: Sequence[float], min_shift: int) -> float | None:
    """Two-sided p from every circular shift of `ys` against `xs` of at least `min_shift`.

    `xs` and `ys` must be consecutive sessions. Exhaustive rather than sampled,
    so it is deterministic; its resolution is one over the number of shifts.
    """
    n = len(xs)
    shifts = range(min_shift, n - min_shift + 1)
    if len(shifts) < 20:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cx = [r - mx for r in rx]
    cy = [r - my for r in ry]
    den = math.sqrt(sum(v * v for v in cx) * sum(v * v for v in cy))
    if not den:
        return None
    observed = abs(sum(a * b for a, b in zip(cx, cy, strict=True))) - 1e-12 * den
    hits = 0
    for k in shifts:
        rolled = cy[k:] + cy[:k]
        if abs(sum(a * b for a, b in zip(cx, rolled, strict=True))) >= observed:
            hits += 1
    return (hits + 1) / (len(shifts) + 1)


def ic_t_stat(ic: float | None, n: int) -> float | None:
    """t for a correlation: ``r √((n − 2) / (1 − r²))``."""
    if ic is None or n < MIN_INDEPENDENT:
        return None
    if abs(ic) >= 1:
        return math.copysign(99.0, ic)
    return ic * math.sqrt((n - 2) / (1 - ic * ic))


def _pairs(scores, fwd, idx) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for t in idx:
        if scores[t] is not None and fwd[t] is not None:
            xs.append(scores[t])
            ys.append(fwd[t])
    return xs, ys


def _r(x: float | None, d: int = 4) -> float | None:
    return None if x is None else round(x, d)


def horizon_stats(scores: Sequence[float | None], fwd: Sequence[float | None], h: int,
                  start: int = 0, trials: int = PERMUTATIONS) -> dict:
    """IC on all days and on every h-th day from `start`, with t and permutation p."""
    n = len(scores)
    xs, ys = _pairs(scores, fwd, range(start, n))
    ixs, iys = _pairs(scores, fwd, range(start, n, h))
    ic_all = spearman(xs, ys)
    ic_ind = spearman(ixs, iys)
    m = len(ixs)
    p = permutation_p(ixs, iys, trials) if m >= MIN_INDEPENDENT else None
    called = [(x, y) for x, y in zip(ixs, iys, strict=True) if x != 0]
    hits = sum((x > 0) == (y > 0) for x, y in called)
    wilson = inference.wilson_interval(hits, len(called)) if called else None
    return {
        "observations": len(xs),
        "independent_observations": m,
        "ic": _r(ic_all),
        "ic_independent": _r(ic_ind),
        "t_stat": _r(ic_t_stat(ic_ind, m), 2),
        "permutation_p": _r(p),
        "permutation_trials": trials,
        "shift_p": _r(shift_p(xs, ys, h)),
        "hit_rate_independent": _r(hits / len(called)) if called else None,
        "hit_rate_wilson_95": None if wilson is None else [_r(wilson.low), _r(wilson.high)],
        "base_rate_up_independent": _r(sum(y > 0 for y in iys) / m) if m else None,
        "mean_forward_return": _r(statistics.fmean(ys), 5) if ys else None,
    }


def bands(scores: Sequence[float | None], fwd: Sequence[float | None], start: int = 0) -> dict:
    """Forward returns grouped by score band, on all scored days.

    ``monotonic`` is true when the mean forward return never falls as the band
    rises, across the bands that have at least five days in them.
    """
    xs, ys = _pairs(scores, fwd, range(start, len(scores)))
    rows = []
    for name, lo, hi in BANDS:
        rs = [y for x, y in zip(xs, ys, strict=True) if lo <= x < hi]
        rows.append({
            "band": name,
            "range": [None if math.isinf(lo) else lo, None if math.isinf(hi) else hi],
            "n": len(rs),
            "mean_return": _r(statistics.fmean(rs), 5) if rs else None,
            "share_up": _r(sum(r > 0 for r in rs) / len(rs)) if rs else None,
        })
    means = [b["mean_return"] for b in rows if b["n"] >= 5]
    monotonic = len(means) >= 2 and all(a <= b for a, b in zip(means, means[1:], strict=False))
    return {"bands": rows, "monotonic": monotonic, "n": len(xs)}


def by_year(scores, fwd_by_h: dict[int, list], days, start: int = 0) -> list[dict]:
    years = sorted({days[t].year for t in range(start, len(scores))})
    out = []
    for y in years:
        idx = [t for t in range(start, len(scores)) if days[t].year == y]
        row: dict = {"year": y, "horizons": {}}
        for h, fwd in fwd_by_h.items():
            xs, ys = _pairs(scores, fwd, idx)
            row["horizons"][str(h)] = {
                "n": len(xs),
                "ic": _r(spearman(xs, ys)) if len(xs) >= MIN_SUBSAMPLE else None,
            }
        out.append(row)
    return out


def by_regime(scores, fwd_by_h: dict[int, list], labels, start: int = 0) -> dict:
    out = {}
    for state in (regime.TRENDING, regime.MEAN_REVERTING, regime.RANDOM):
        idx = [t for t in range(start, len(scores)) if labels[t] == state]
        cell: dict = {"days": len(idx), "horizons": {}}
        for h, fwd in fwd_by_h.items():
            xs, ys = _pairs(scores, fwd, idx)
            cell["horizons"][str(h)] = {
                "n": len(xs),
                "ic": _r(spearman(xs, ys)) if len(xs) >= MIN_SUBSAMPLE else None,
            }
        out[state] = cell
    return out


# ----------------------------------------------------------- rule economics


def positions(scores: Sequence[float | None], rule: str) -> list[float]:
    """Target position from each score: long/flat above the neutral band, or its sign."""
    out = []
    for s in scores:
        if s is None:
            out.append(0.0)
        elif rule == "long_flat":
            out.append(1.0 if s > NEUTRAL else 0.0)
        elif rule == "long_short":
            out.append(1.0 if s > 0 else -1.0 if s < 0 else 0.0)
        elif rule == "buy_and_hold":
            out.append(1.0)
        else:
            raise ValueError(f"unknown rule {rule!r}")
    return out


def rule_returns(pos: Sequence[float], next_rets: Sequence[float],
                 cost_bps: float) -> tuple[list[float], float]:
    """Daily net returns of holding ``pos[i]`` over ``next_rets[i]``, and total turnover.

    The position is set at the close the score was computed on and earns the
    next session's return; every unit of change, including the first entry
    from flat, pays `cost_bps`.
    """
    cost = cost_bps / 10_000
    prev, turnover, out = 0.0, 0.0, []
    for p, r in zip(pos, next_rets, strict=True):
        change = abs(p - prev)
        turnover += change
        out.append(p * r - change * cost)
        prev = p
    return out, turnover


def _max_drawdown(rets: Sequence[float]) -> float:
    peak = equity = 1.0
    worst = 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def rule_stats(pos: Sequence[float], next_rets: Sequence[float], cost_bps: float,
               n_trials: int) -> dict:
    net, turnover = rule_returns(pos, next_rets, cost_bps)
    gross, _ = rule_returns(pos, next_rets, 0.0)
    n = len(net)
    growth = math.prod(1 + r for r in net)
    gross_growth = math.prod(1 + r for r in gross)
    vol = statistics.stdev(net) * math.sqrt(TRADING_DAYS) if n > 1 else None
    mean = statistics.fmean(net) if net else 0.0
    sharpe = mean * TRADING_DAYS / vol if vol else None
    inf = inference.sharpe_inference(net, n_trials=n_trials)
    return {
        "sessions": n,
        "annual_return": _r(growth ** (TRADING_DAYS / n) - 1) if n and growth > 0 else None,
        "annual_return_gross": _r(gross_growth ** (TRADING_DAYS / n) - 1)
        if n and gross_growth > 0 else None,
        "total_return": _r(growth - 1),
        "annual_vol": _r(vol),
        "sharpe": _r(sharpe, 3),
        "max_drawdown": _r(_max_drawdown(net)),
        "turnover_annual": _r(turnover * TRADING_DAYS / n, 2) if n else None,
        "exposure": _r(statistics.fmean(abs(p) for p in pos)) if pos else None,
        "psr": None if inf is None else inf["psr_vs_zero"],
        "dsr": None if inf is None else inf["dsr"],
        "n_trials": n_trials,
    }


def _equity(rets: Sequence[float]) -> list[float]:
    out, e = [], 1.0
    for r in rets:
        e *= 1 + r
        out.append(round(e, 5))
    return out


def _downsample(n: int, limit: int = MAX_CURVE_POINTS) -> list[int]:
    if n <= limit:
        return list(range(n))
    step = (n - 1) / (limit - 1)
    return sorted({round(i * step) for i in range(limit)})


# ------------------------------------------------------------------ verdict


def _verdict(label: str, hz: dict, rules: dict, alpha: float) -> tuple[str, bool]:
    best_dsr = max((r["dsr"] or 0.0) for r in rules.values())
    inverted_dsr = rules.get("long_short", {}).get("dsr_inverted") or 0.0
    passing, against = [], []
    for h, s in hz.items():
        t, p, q = s["t_stat"], s["permutation_p"], s["shift_p"]
        if t is None or p is None or q is None:
            continue
        p = max(p, q)
        if t >= T_BAR and p < alpha and best_dsr > DSR_BAR:
            passing.append(h)
        elif t <= -T_BAR and p < alpha and inverted_dsr > DSR_BAR:
            against.append(h)
    if passing:
        h = passing[0]
        s = hz[h]
        return (f"{label[0].upper() + label[1:]} works here: at {h} sessions the "
                f"non-overlapping IC is "
                f"{s['ic_independent']:+.2f} (t {s['t_stat']:.1f}; p "
                f"{max(s['permutation_p'], s['shift_p']):.4f} on the stricter of two "
                f"resampling tests, against a corrected {alpha:.4f}), and a rule trading it "
                f"keeps a deflated Sharpe of {best_dsr:.2f}."), True
    if against:
        h = against[0]
        s = hz[h]
        return (f"Evidence against {label} here: at {h} sessions its scores have pointed "
                f"the wrong way (IC {s['ic_independent']:+.2f}, t {s['t_stat']:.1f}) more "
                f"reliably than chance allows, and trading it backwards keeps a deflated "
                f"Sharpe of {inverted_dsr:.2f}."), False
    tested = [s for s in hz.values() if s["t_stat"] is not None]
    if not tested:
        return f"Too little history to test {label} on this stock.", False
    strongest = max(tested, key=lambda s: abs(s["t_stat"]))
    p = max(strongest["permutation_p"] or 1.0, strongest["shift_p"] or 1.0)
    if abs(strongest["t_stat"]) < T_BAR:
        why = f"the strongest non-overlapping t is {strongest['t_stat']:.1f}, inside ±{T_BAR:g}"
    elif p >= alpha:
        why = (f"its best t of {strongest['t_stat']:.1f} has p {p:.3f} on the stricter "
               f"resampling test, above the {alpha:.4f} that {round(FAMILY_ALPHA / alpha)} "
               "tests require")
    elif strongest["t_stat"] < 0:
        why = (f"its scores have pointed the wrong way (t {strongest['t_stat']:.1f}), but "
               f"trading them backwards does not keep a deflated Sharpe above {DSR_BAR:g} "
               f"(it keeps {inverted_dsr:.2f}), so not even that is evidence")
    else:
        why = (f"the correlation clears, but no rule trading it keeps a deflated Sharpe "
               f"above {DSR_BAR:g} (best {best_dsr:.2f}) after costs")
    return f"No evidence that {label} has predicted this stock: {why}.", False


# --------------------------------------------------------------------- main


def test_families(bars: list[Bar], bench: list[Bar] | None, horizons: Sequence[int] = (5, 21),
                  cost_bps: float = 10) -> dict | None:
    """Walk-forward test of each price family on this stock. None below `MIN_BARS`.

    JSON-ready. ``tests_run`` counts every (family × horizon × rule) combination
    and is the N used both for the Bonferroni alpha on the permutation tests and
    for the Deflated Sharpe on the rules.
    """
    if len(bars) < MIN_BARS:
        return None
    horizons = tuple(sorted({int(h) for h in horizons if int(h) > 0}))
    if not horizons:
        raise ValueError("at least one positive horizon is required")
    closes = [b.close for b in bars]
    days = [b.day for b in bars]
    n = len(bars)
    scores = score_series(bars, bench if bench else None)
    labels = regime_series(closes)
    fwd = {h: forward_returns(closes, h) for h in horizons}

    rule_names = ("long_flat", "long_short")
    tests_run = len(scores) * len(horizons) * len(rule_names)
    alpha = FAMILY_ALPHA / tests_run
    trials = max(PERMUTATIONS, math.ceil(4 / alpha))

    # Rule days: positions set at t, earning close[t+1]/close[t]-1.
    rule_idx = list(range(WARMUP, n - 1))
    next_rets = [closes[t + 1] / closes[t] - 1 for t in rule_idx]
    bh = rule_stats(positions([1.0] * len(rule_idx), "buy_and_hold"), next_rets, cost_bps, 1)
    bh_net, _ = rule_returns([1.0] * len(rule_idx), next_rets, cost_bps)
    bh_curve = _equity(bh_net)
    keep = _downsample(len(rule_idx))
    curves: dict[str, list] = {
        "dates": [days[rule_idx[i] + 1].isoformat() for i in keep],
        "buy_and_hold": [bh_curve[i] for i in keep],
    }

    families = []
    for name, series in scores.items():
        hz = {str(h): horizon_stats(series, fwd[h], h, WARMUP, trials) for h in horizons}
        rules = {}
        for rule in rule_names:
            pos = positions([series[t] for t in rule_idx], rule)
            rules[rule] = rule_stats(pos, next_rets, cost_bps, tests_run)
            if rule == "long_short":
                # "Evidence against" must clear the mirror of the bar "works" clears:
                # trading the signal backwards has to survive deflation too.
                inverted = rule_stats([-x for x in pos], next_rets, cost_bps, tests_run)
                rules[rule]["dsr_inverted"] = inverted["dsr"]
            if rule == "long_flat":
                eq = _equity(rule_returns(pos, next_rets, cost_bps)[0])
                curves[f"{name}_long_flat"] = [eq[i] for i in keep]
        label = LABELS[name]
        verdict, works = _verdict(label, hz, rules, alpha)
        families.append({
            "name": name,
            "label": label,
            "horizons": hz,
            "bands": {str(h): bands(series, fwd[h], WARMUP) for h in horizons},
            "by_year": by_year(series, fwd, days, WARMUP),
            "rules": rules,
            "by_regime": by_regime(series, fwd, labels, WARMUP),
            "verdict": verdict,
            "works": works,
        })

    working = [f["label"] for f in families if f["works"]]
    scored = n - WARMUP
    if working:
        reading = (f"Of {len(families)} price signals tested walk-forward over {scored} sessions, "
                   f"{', '.join(working)} cleared every bar, after charging for "
                   f"{tests_run} tests and {cost_bps:g} bps per trade. Past evidence on one "
                   "stock is still one stock.")
    else:
        reading = (f"None of the {len(families)} price signals tested walk-forward over {scored} "
                   f"sessions shows evidence of predicting this stock once {tests_run} tests "
                   f"and {cost_bps:g} bps per trade are charged for. Treat their scores on the "
                   "page as descriptions of the chart, not forecasts.")

    return {
        "horizons": list(horizons),
        "cost_bps": cost_bps,
        "observations": scored,
        "period": {"start": days[WARMUP].isoformat(), "end": days[-1].isoformat()},
        "tests_run": tests_run,
        "bonferroni_alpha": round(alpha, 6),
        "bonferroni_z": round(inference.bonferroni_threshold(tests_run, FAMILY_ALPHA), 3),
        "permutation_trials": trials,
        "families": families,
        "buy_and_hold": bh,
        "regime_days": {s: sum(lab == s for lab in labels[WARMUP:])
                        for s in (regime.TRENDING, regime.MEAN_REVERTING, regime.RANDOM)},
        "equity_curves": curves,
        "reading": reading,
    }


# Not a pytest test, despite the name the feature asks for.
test_families.__test__ = False  # type: ignore[attr-defined]
