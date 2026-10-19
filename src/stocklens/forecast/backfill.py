"""A simulated track record: what the ledger would have said, had it been running.

Simulated, not live
-------------------
The live ledger only starts the day forecasts start being recorded, and a
21-session horizon means its first month has nothing to show. This module
fills the gap by replaying history: at every `step`-th session it calls the
same `make_forecast` the live path calls, on the bars that existed then, and
scores the call against the close `HORIZON` sessions later.

That makes it a **backfill**, and it is weaker evidence than the live record
for one reason no amount of care removes: the model was written by someone who
had already seen this history. Any page that shows it must label it
"simulated (backfilled)" and keep it apart from the live, append-only record
whose forecasts were stored before their outcomes existed.

Point-in-time discipline
------------------------
* The forecast at session ``t`` sees ``bars[: t + 1]`` and ``bench[: t + 1]``
  and nothing else. Mutating any later bar cannot change it; the tests check
  exactly that.
* The outcome is ``bars[t + HORIZON].close`` — a fixed session count, not a
  calendar date, so a holiday never moves the resolution.
* Decision sessions are anchored at `warmup` from the start of the series, so
  appending new bars adds forecasts at the end and leaves every earlier one
  untouched.
* Only price families are used (see `model`), because filings and news carry
  no reliable first-knowable date in the open sources.
"""

from __future__ import annotations

from ..data.base import Bar
from .model import HORIZON, MODEL_VERSION, make_forecast
from .scoring import score

METHOD = (
    "Simulated (backfilled) record. At every {step}th session after a {warmup}-session warm-up, "
    "the live forecast function is called on the bars that existed at that session only "
    "(bars[:t+1]), and the call is resolved against the close {horizon} sessions later "
    "(bars[t+{horizon}]). Price families only, model {version}. The model was designed after "
    "this history was observable, so treat this as weaker evidence than the live ledger."
)

_KEEP = ("made_on", "p_up", "direction", "score", "price", "range_low", "range_high",
         "regime", "features")


def _align(bars: list[Bar], bench: list[Bar] | None) -> tuple[list[Bar], list[Bar]]:
    """Trim both to a common tail length, as the evaluation harness does."""
    if not bench:
        return bars, []
    n = min(len(bars), len(bench))
    return bars[-n:], bench[-n:]


def resolve(forecast: dict, outcome_price: float) -> dict:
    """Attach the outcome fields to a forecast dict (a new dict; the input is untouched)."""
    low, high = forecast.get("range_low"), forecast.get("range_high")
    return {
        **forecast,
        "outcome_price": round(outcome_price, 4),
        "outcome_up": outcome_price > forecast["price"],
        "in_range": None if low is None or high is None else low <= outcome_price <= high,
    }


def _walk(symbol: str, bars: list[Bar], bench: list[Bar], step: int, warmup: int) -> list[dict]:
    out = []
    for t in range(warmup, len(bars) - HORIZON, step):
        f = make_forecast(bars[: t + 1], bench[: t + 1] if bench else None)
        if f is None:
            continue
        trimmed = {"symbol": symbol, **{k: f[k] for k in _KEEP}}
        row = resolve(trimmed, bars[t + HORIZON].close)
        row["resolved_on"] = bars[t + HORIZON].day.isoformat()
        out.append(row)
    return out


def _package(forecasts: list[dict], symbols: list[str], step: int, warmup: int) -> dict:
    days = sorted({f["made_on"] for f in forecasts})
    return {
        "kind": "simulated",
        "model_version": MODEL_VERSION,
        "horizon": HORIZON,
        "forecasts": forecasts,
        "scoring": score(forecasts),
        "symbols": symbols,
        "period": {"start": days[0] if days else None, "end": days[-1] if days else None},
        "method": METHOD.format(step=step, warmup=warmup, horizon=HORIZON,
                                version=MODEL_VERSION),
    }


def track_record(panel: dict[str, list[Bar]], bench: list[Bar], step: int = 5,
                 warmup: int = 260) -> dict:
    """Walk-forward simulated record across a panel of symbols, scored as one sample."""
    if step < 1:
        raise ValueError("step must be at least 1")
    forecasts: list[dict] = []
    for symbol, bars in panel.items():
        b, m = _align(bars, bench)
        forecasts.extend(_walk(symbol, b, m, step, warmup))
    forecasts.sort(key=lambda f: (f["made_on"], f["symbol"]))
    return _package(forecasts, list(panel), step, warmup)


def symbol_record(bars: list[Bar], bench: list[Bar] | None, step: int = 5,
                  warmup: int = 260, symbol: str = "") -> dict:
    """The same simulated record for one symbol."""
    if step < 1:
        raise ValueError("step must be at least 1")
    b, m = _align(bars, bench)
    return _package(_walk(symbol, b, m, step, warmup), [symbol] if symbol else [], step, warmup)
