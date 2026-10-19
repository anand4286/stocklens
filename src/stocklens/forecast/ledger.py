"""Resolving the live ledger: outcomes are filled in once, by session count.

The live record is the strong evidence, because every row in it was written
before its outcome existed. Resolution keeps it that way:

* A forecast resolves only when at least `horizon` bars exist *after* its
  `made_on` session, and the outcome is the close on exactly the
  `horizon`-th of them. Counting bars rather than calendar days means a
  holiday or a data gap never shortens the horizon.
* Resolution is **write-once** (enforced in `Database.resolve_forecast`).
  Re-running the resolver, or running it on revised bars, cannot rewrite an
  outcome that has already been scored.
* The resolver runs whenever a symbol's bars are fetched anyway, so no
  scheduler is needed: viewing a stock settles whatever of its record is due.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ..data.base import Bar

if TYPE_CHECKING:
    from ..db import Database


def _decision_index(bars: list[Bar], made_on: date) -> int | None:
    """Index of the last bar on or before `made_on` (bars are oldest first)."""
    idx = None
    for i, b in enumerate(bars):
        if b.day > made_on:
            break
        idx = i
    return idx


def resolve_due(db: Database, symbol: str, bars: list[Bar]) -> int:
    """Resolve every unresolved forecast for `symbol` whose horizon has elapsed in `bars`.

    Returns how many were resolved by this call.
    """
    if not bars:
        return 0
    resolved = 0
    for f in db.due_forecasts(symbol):
        made_on = date.fromisoformat(str(f["made_on"]))
        i = _decision_index(bars, made_on)
        if i is None or bars[i].day != made_on:
            # The decision session is not in this window of bars (too old, or the
            # history was revised). Resolving against a different session would
            # score a forecast against a horizon it never committed to.
            continue
        target = i + int(f["horizon"])
        if target >= len(bars):
            continue
        outcome = bars[target].close
        low, high = f.get("range_low"), f.get("range_high")
        in_range = None if low is None or high is None else low <= outcome <= high
        if db.resolve_forecast(f["id"], outcome, outcome > f["price"], in_range):
            resolved += 1
    return resolved
