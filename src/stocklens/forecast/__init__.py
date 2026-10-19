"""The forecast ledger: falsifiable probabilistic calls, scored in public.

A data aggregator can show an analyst's price target. It cannot show how often
that analyst's targets came true, because nobody kept score. This package
keeps score on StockLens itself:

* `model` — turn the price-family composite into a committed forecast:
  direction, probability, horizon and a one-sigma range, from bars known on
  the decision date only.
* `ledger` — resolve recorded forecasts once their horizon has passed. The
  rows live in `stocklens.db` and are append-only; a resolution is write-once.
* `scoring` — proper scoring rules (Brier, log loss), calibration, range
  coverage and slices, each with its sample size and the interval it supports.
* `backfill` — a *simulated* point-in-time track record, for the years before
  the live ledger existed. It is labelled as such wherever it is shown.
"""

from __future__ import annotations

from .model import HORIZON, MODEL_VERSION, make_forecast

__all__ = ["HORIZON", "MODEL_VERSION", "make_forecast"]
