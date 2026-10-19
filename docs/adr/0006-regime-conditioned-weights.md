# ADR 0006 — Regime-conditioned family weights

**Status:** accepted, pending evidence from `stocklens-evaluate`

## Context
The technical family is RSI-led and assumes short-horizon mean reversion;
momentum assumes persistence. Blending them with fixed weights is an implicit
bet that the regime never changes, and the two families argue against each
other exactly when one of them is right.

## Decision
`report.build` classifies the trend regime (corrected Hurst and variance ratio,
which must agree outside their noise bands) before blending, and passes
`regime.tilt_weights(DEFAULT_WEIGHTS, regime)` to `blend`. Multipliers are
picked, bounded to 0.5–1.5×, touch only the three price families, and are
renormalised so total weight — and therefore coverage and confidence — is
unchanged. A `random` regime leaves the weights exactly as published.

## Consequences
- The composite is no longer a function of the families alone. To keep it
  explainable, the report carries `base_weights`, `regime_weights` and the
  regime; the page shows both columns and the agent sees the tilted weights in
  `get_signal_breakdown` and `get_quant`.
- The detector is conservative (random walks: 100/100 "random"), so on most
  symbols most days the tilt is a no-op. That is the safe failure.
- The multipliers are a prior, not a result. The evaluation harness computes
  every statistic under both weightings and reports `regime_ic_delta`; if that
  is not positive with |t| ≥ 2 on real data, this ADR should be superseded and
  the tilt shown as information only.
