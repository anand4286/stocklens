"""Walk-forward evaluation of the price-based families.

    stocklens-evaluate --symbols AAPL MSFT NVDA JPM XOM KO --horizon 5

For every session and every symbol, score the families using only bars up to
that session, then compare against the forward return over `horizon`
sessions. The headline numbers are the ones that convert to money, not a
directional accuracy that a rising market inflates:

* **Rank IC** — Spearman correlation between score and forward return across
  symbols on each day, averaged over days, with a t-statistic. Published
  cross-sectional equity models live around 0.02-0.08.
* **Top-minus-bottom spread** — mean forward return of the top third by score
  minus the bottom third.
* **Hit rate vs base rate** — how often the sign was right, next to how often
  "always up" would have been right. A hit rate is meaningless without it.

Only price families are evaluated: filings and news are not point-in-time in
the open data sources, and scoring them historically would leak the future.
Overlapping horizons make consecutive days dependent, so the t-statistic is
computed on non-overlapping days (every `horizon`-th session).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

from .analytics import signals
from .analytics.composite import DEFAULT_WEIGHTS
from .config import get_settings
from .data import build_market_data, normalise_symbol

PRICE_FAMILIES = ("technical", "momentum", "relative_strength")


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def price_score(bars, bench) -> float | None:
    fams = [signals.technical(bars), signals.momentum(bars),
            signals.relative_strength(bars, bench)]
    used = [(DEFAULT_WEIGHTS[f.name], f.score) for f in fams if f.available]
    total = sum(w for w, _ in used)
    return sum(w * s for w, s in used) / total if total else None


def evaluate(symbols: list[str], horizon: int = 5, history: int = 750,
             warmup: int = 260, step: int = 1) -> dict:
    settings = get_settings()
    market = build_market_data(settings)
    bench = market.bars(settings.benchmark_symbol, history)
    panel = {s: market.bars(s, history) for s in symbols}
    length = min(len(b) for b in [bench, *panel.values()])
    panel = {s: b[-length:] for s, b in panel.items()}
    bench = bench[-length:]

    daily_ic, spreads, hits, ups, n_obs = [], [], 0, 0, 0
    for t in range(warmup, length - horizon, step):
        scores, fwd = [], []
        for bars in panel.values():
            score = price_score(bars[: t + 1], bench[: t + 1])
            if score is None:
                continue
            ret = bars[t + horizon].close / bars[t].close - 1
            scores.append(score)
            fwd.append(ret)
            hits += (score > 0) == (ret > 0)
            ups += ret > 0
            n_obs += 1
        ic = spearman(scores, fwd)
        if ic is not None:
            daily_ic.append((t, ic))
        if len(scores) >= 3:
            ranked = sorted(zip(scores, fwd, strict=True))
            k = max(1, len(ranked) // 3)
            spreads.append(statistics.fmean(r for _, r in ranked[-k:])
                           - statistics.fmean(r for _, r in ranked[:k]))

    independent = [ic for t, ic in daily_ic if (t - warmup) % horizon == 0]
    mean_ic = statistics.fmean(ic for _, ic in daily_ic) if daily_ic else None
    t_stat = None
    if len(independent) > 2 and statistics.stdev(independent) > 0:
        t_stat = statistics.fmean(independent) / (
            statistics.stdev(independent) / math.sqrt(len(independent)))
    return {
        "symbols": symbols,
        "provider": market.sources["prices"],
        "horizon_sessions": horizon,
        "days_scored": len(daily_ic),
        "independent_days": len(independent),
        "observations": n_obs,
        "rank_ic_mean": None if mean_ic is None else round(mean_ic, 4),
        "rank_ic_t_stat": None if t_stat is None else round(t_stat, 2),
        "top_minus_bottom_mean": round(statistics.fmean(spreads), 5) if spreads else None,
        "hit_rate": round(hits / n_obs, 4) if n_obs else None,
        "base_rate_up": round(ups / n_obs, 4) if n_obs else None,
        "reading": ("Treat |t| < 2 as no evidence of an edge. On demo data, which is a random "
                    "walk by construction, that is the correct answer."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--symbols", nargs="+", default=[
        "AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "KO", "PFE", "CAT", "WMT"])
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--history", type=int, default=750)
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        symbols = [normalise_symbol(s) for s in args.symbols]
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(json.dumps(evaluate(symbols, args.horizon, args.history, step=args.step), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
