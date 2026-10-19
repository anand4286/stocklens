# ADR 0005 — Numerics implemented, not imported

**Status:** accepted

## Context
The deep-analytics layer needs eigendecomposition, Cholesky, a constrained
quadratic optimiser, maximum-likelihood GARCH, hierarchical clustering and the
normal quantile. NumPy and SciPy do all of it faster. They also add ~120 MB to
a container whose whole surface is otherwise a few MB of Python, and they
replace visible arithmetic with calls whose failure modes live in Fortran.

## Decision
Everything under `quant/` and `research/` is pure Python over `list[float]`
and the standard library (`math`, `statistics.NormalDist`). Each routine
documents where it is exact, where it is iterative, its convergence rule, and
what it returns on degenerate input (a singular matrix, a flat series, a fit
that hits the stationarity wall) — usually `None`, never an exception into the
page.

## Consequences
- The image and the dependency audit stay small; `pip-audit` has nothing new
  to watch.
- Every estimator is readable next to the paper it cites, which is the point of
  an explainable site.
- Cost is speed. The bound is explicit: the portfolio lab caps baskets at 25
  symbols (≈0.6 s for 25 × 756 days), a stock report with every model is
  ≈90 ms, and the evaluation harness re-runs the regime per symbol per day in
  about a second for six symbols.
- If a future feature needs N ≫ 25 or intraday data, NumPy goes in behind the
  same function signatures — the callers take lists and return dicts, so the
  swap is local.
