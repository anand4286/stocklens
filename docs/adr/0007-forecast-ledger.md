# ADR 0007 — A falsifiable forecast ledger

**Status:** accepted

## Context
Stock sites print price targets and ratings and never revisit them. A site
whose premise is "show your working" has to do the opposite: say in advance
what it expects, in a form that can be wrong, and publish how often it was.

## Decision
- Every stock report builds a forecast from the bars it already has:
  P(close higher in 21 sessions), direction, a one-sigma range, the feature
  vector, the regime and a `model_version`.
- `GET /report` writes it to the append-only `forecasts` table. A unique key
  on (symbol, made_on, model_version) makes the first forecast of a session
  the one that counts; a refresh cannot replace it.
- Resolution is write-once (`UPDATE … WHERE resolved_at IS NULL`) and lazy: the
  next view of a symbol resolves any forecast whose 21 sessions have elapsed.
- The probability map is deliberately shrunk (`0.5 + 0.5·tanh(0.35·score)`,
  roughly 33–67%). The constant is picked; calibration grades it.
- Grading: Brier skill against climatology (quoting the base rate), a
  reliability table with Wilson intervals, range coverage against 68%, hit rate
  beside "always up", and a **block t-test** over independent 21-session
  blocks. "Skill" needs all of them.
- A *simulated* record walks the same model point-in-time over a fixed,
  published basket so there is something to grade on day one. It is labelled
  "simulated" everywhere and never pooled with the live ledger.

## Consequences
- The site can be wrong in public, which is the point.
- Any change to weights, the map, families, tilt, vol estimator or horizon
  bumps `model_version`, so old forecasts stay graded against the model that
  made them.
- Lazy resolution needs a page view; a scheduled resolver is the natural next
  step and needs no schema change.
- The block test was added after a 5-symbol random-walk basket earned "skill"
  on the plain interval. Same-day forecasts share one market, and 5-session
  steps with 21-session outcomes overlap; counting rows overstated the evidence.
