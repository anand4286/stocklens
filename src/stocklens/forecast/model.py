"""From a composite score to a forecast someone can be held to.

What a forecast commits to
--------------------------
A score of +0.31 is an opinion. It cannot be wrong, because it never said what
would happen. A forecast says, before the outcome is knowable:

* a **direction** over a fixed horizon (`HORIZON` sessions),
* a **probability** that the price is higher at the end of it,
* a **one-sigma range** the price is expected to finish inside about 68% of
  the time,
* the **feature vector** and **regime** that produced it, and
* the **model version** in force.

Every one of those can be checked against what the price did. That is the
difference between a forecast and a rating.

Only price families
-------------------
The forecast uses technical, momentum and relative strength, blended with the
published `DEFAULT_WEIGHTS` tilted for the regime exactly as the page does.
Filings and news are left out on purpose: the open data sources do not say
when a filing figure or a headline first became knowable, so a historical
forecast that used them would quietly read the future, and the simulated
track record (`backfill`) would flatter the live one. A model graded on a
different input set from the one it is used with is not being graded.

The probability map, and why it is shrunk
-----------------------------------------
::

    p_up = 0.5 + 0.5 * tanh(K * score)        K = 0.35

With the score on [-1, +1] this keeps p_up inside roughly [0.33, 0.67]. `K` is
**picked, not fitted**, and picked small. Nothing in the record yet earns a
steeper map: on the evaluation harness the price families' rank IC sits in the
range where published equity models live, which is a few points of hit rate,
not twenty. An overconfident map scores worse on every proper scoring rule
than a timid one with the same ranking, and position sizing consumes these
probabilities, so the cost of being too timid is a little money left on the
table and the cost of being too bold is sizing every bet too large.

`K` is not defended here; it is graded in `scoring`. The reliability table
answers the only question that matters about it: when the ledger said 60%,
did the price rise 60% of the time? If the observed frequencies fan out wider
than the predictions, `K` is too small; if they collapse to the base rate,
the score carries no information and no `K` would help.

The range
---------
The one-sigma range is ``price * exp(±σ√H)`` with σ the RiskMetrics EWMA daily
volatility (λ = 0.94). EWMA rather than the page's GARCH fit because a
backfill re-estimates it at every decision date and GARCH's maximum
likelihood search is two orders of magnitude slower; the two track each other
closely at a one-month horizon, and `scoring` measures the coverage either
way. Drift is ignored: over 21 sessions it is a small fraction of σ√H and
estimating it adds more noise than it removes.

Versioning
----------
`MODEL_VERSION` names the mapping from bars to forecast. **Bump it whenever a
change could alter any committed field** — weights, `K`, the families used,
the regime tilt, the volatility estimator, `HORIZON`. The ledger's uniqueness
key includes the version, so a new version starts a new, separately scored
record instead of silently mixing two models' calls into one calibration.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from ..analytics import signals
from ..analytics.composite import DEFAULT_WEIGHTS
from ..analytics.risk import TRADING_DAYS, ewma_vol, log_returns
from ..data.base import Bar
from ..quant import regime

#: Sessions between the decision and the outcome: about one calendar month.
#: Long enough that a day's noise does not decide the call, short enough that
#: a year of decisions resolves into a usable sample.
HORIZON = 21

#: See "Versioning" above. A string, not a number, so a fork can namespace it.
MODEL_VERSION = "price-v1"

#: Steepness of the score-to-probability map. Picked, deliberately shrunk;
#: graded by the reliability table in `scoring`.
K = 0.35

PRICE_FAMILIES = ("technical", "momentum", "relative_strength")


def probability_up(score: float) -> float:
    """The documented, shrunk map from a composite on [-1, 1] to P(up)."""
    return 0.5 + 0.5 * math.tanh(K * score)


def add_business_days(start: date, sessions: int) -> date:
    """Weekdays only. Holidays are ignored, so this is the *earliest* resolution date."""
    day = start
    remaining = sessions
    while remaining > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


def _sigma_daily(bars: list[Bar]) -> float | None:
    annual = ewma_vol(log_returns([b.close for b in bars]))
    return None if annual is None else annual / math.sqrt(TRADING_DAYS)


def make_forecast(bars: list[Bar], bench: list[Bar] | None) -> dict | None:
    """A committed forecast from `bars` (oldest first), the last of which is the decision date.

    Every input is read from `bars` and `bench` as passed; nothing after
    ``bars[-1]`` exists as far as this function knows. Callers doing
    point-in-time work pass ``bars[: t + 1]``. Returns None when no price
    family has an opinion — there is nothing to commit to.
    """
    if not bars:
        return None
    fams = [signals.technical(bars), signals.momentum(bars),
            signals.relative_strength(bars, bench or [])]
    reg = regime.classify([b.close for b in bars])
    weights = regime.tilt_weights(DEFAULT_WEIGHTS, reg)
    used = [(weights[f.name], f.score) for f in fams if f.score is not None]
    total = sum(w for w, _ in used)
    if not total:
        return None
    score = sum(w * s for w, s in used) / total
    p_up = probability_up(score)

    price = bars[-1].close
    sigma = _sigma_daily(bars)
    if sigma is None:
        low = high = None
    else:
        spread = sigma * math.sqrt(HORIZON)
        low, high = round(price * math.exp(-spread), 4), round(price * math.exp(spread), 4)

    made_on = bars[-1].day
    return {
        "made_on": made_on.isoformat(),
        "horizon": HORIZON,
        "resolves_on_or_after": add_business_days(made_on, HORIZON).isoformat(),
        "direction": "up" if p_up >= 0.5 else "down",
        "p_up": round(p_up, 4),
        "score": round(score, 4),
        "range_low": low,
        "range_high": high,
        "price": round(price, 4),
        "features": {f.name: None if f.score is None else round(f.score, 4) for f in fams},
        "regime": reg["trend"],
        "model_version": MODEL_VERSION,
    }
