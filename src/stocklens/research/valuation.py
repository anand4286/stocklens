"""Multiples, a discounted cash flow that shows its own sensitivity, and the
growth the price already assumes.

Why the sensitivity grid is the output, not a fair value
--------------------------------------------------------
A DCF produces one number, and that number is almost entirely a restatement of
its assumptions. Terminal value is usually 60-80% of it, and terminal value is
`FCF x (1+g) / (r-g)` — a ratio of two guesses whose *difference* sits in the
denominator. At r=9% and g=3% the multiple is 17.2x; move g to 4% and it is
20.6x, a fifth more company from a rounding-sized change to a number nobody can
know. So the honest output is the grid, and `terminal_share` says how much of
the middle cell is the terminal value. There is deliberately no `fair_value`.

Why a reverse DCF
-----------------
The forward DCF asks "what is it worth if it grows at g?", and the reader has to
supply g. The reverse question needs no forecast at all: at a 9% discount rate
and 2.5% terminal growth, what constant ten-year growth in free cash flow makes
the model equal today's price? That number, set beside what the company has
actually grown at, is the cleanest statement of what a buyer is betting on.

Conventions
-----------
Free cash flow is operating cash flow less capex. Operating cash flow is after
interest, so this is cash to *equity*: it is discounted at an equity rate and
divided by the share count directly, with no further net-debt adjustment —
subtracting debt again would charge for the interest twice.

What cannot be computed here
----------------------------
Forward P/E needs consensus estimates, which nothing in this system carries. It
is withheld rather than approximated from trailing earnings, which would be the
trailing multiple under another name.
"""

from __future__ import annotations

import math
from datetime import date

from ..data.base import Fundamentals

#: Years of explicit forecast before the terminal value takes over. Long enough
#: that a high-growth case is not entirely terminal value, short enough that
#: nobody pretends to see further.
FORECAST_YEARS = 10

#: Discount rates span a low-beta utility to a speculative equity; terminal
#: rates span sub-inflation decline to roughly long-run nominal GDP, above which
#: a company grows without limit into the whole economy.
DISCOUNT_RATES = (0.07, 0.08, 0.09, 0.10, 0.11, 0.12)
TERMINAL_RATES = (0.015, 0.020, 0.025, 0.030, 0.035, 0.040)

#: The reverse DCF's fixed assumptions, and the cell `terminal_share` reads.
BASE_DISCOUNT, BASE_TERMINAL = 0.09, 0.025

#: Terminal growth must stay below the discount rate or the Gordon series
#: diverges. Arithmetic, not judgement.
MIN_SPREAD = 0.01

#: No company compounds faster than this for a decade; a DCF that assumes it
#: will is arithmetic about a company that does not exist.
GROWTH_CAP = 0.25
GROWTH_FLOOR = -0.10
#: Used, and labelled as such, when the company has no growth history.
GDP_ANCHOR = 0.04

#: Bisection bounds for the reverse DCF: -50% to +100% a year for ten years
#: covers every price a positive cash flow can support in practice.
IMPLIED_LO, IMPLIED_HI = -0.50, 1.00


def _r(x: float | None, n: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, n)


def _shares(f: Fundamentals) -> float | None:
    for value in (f.shares_outstanding, f.shares_diluted):
        if value and value > 0:
            return value
    return None


def market_cap(f: Fundamentals, price: float | None) -> float | None:
    shares = _shares(f)
    if price is None or price <= 0 or shares is None:
        return None
    return price * shares


def enterprise_value(f: Fundamentals, cap: float | None) -> float | None:
    """Equity plus debt, less cash: what the whole business costs.

    The right numerator for revenue and EBIT, which are earned before interest
    and so belong to lenders and owners together.
    """
    if cap is None:
        return None
    return cap + (f.long_term_debt or 0.0) - (f.cash or 0.0)


def free_cash_flow(f: Fundamentals) -> float | None:
    if f.operating_cash_flow is None or f.capex is None:
        return None
    return f.operating_cash_flow - abs(f.capex)


def _ebit(f: Fundamentals) -> float | None:
    if f.operating_income is not None:
        return f.operating_income
    if f.pretax_income is None:
        return None
    return f.pretax_income + (f.interest_expense or 0.0)


def multiples(f: Fundamentals, price: float | None) -> tuple[dict, dict]:
    """Every multiple the filing supports, and a reason for each one absent."""
    cap = market_cap(f, price)
    ev = enterprise_value(f, cap)
    out: dict = {"market_cap": _r(cap, 0), "enterprise_value": _r(ev, 0)}
    withheld: dict[str, str] = {}
    no_cap = "needs a price and a share count" if cap is None else None

    def ratio(name: str, num: float | None, den: float | None, why: str, n: int = 2) -> None:
        if num is None or den is None or den <= 0:
            out[name] = None
            withheld[name] = no_cap if num is None and no_cap else why
            return
        out[name] = _r(num / den, n)

    ratio("pe", cap, f.net_income, "needs positive net income")
    ratio("ps", cap, f.revenue, "needs revenue")
    ratio("pb", cap, f.equity, "needs positive book equity")
    ratio("ev_ebit", ev, _ebit(f), "needs positive operating profit")
    ratio("ev_sales", ev, f.revenue, "needs revenue")

    fcf = free_cash_flow(f)
    for name, num, why in (("fcf_yield", fcf, "needs operating cash flow and capex"),
                           ("earnings_yield", f.net_income, "needs net income")):
        if cap is None or num is None:
            out[name] = None
            withheld[name] = no_cap or why
        else:
            out[name] = _r(num / cap)

    out["forward_pe"] = None
    withheld["forward_pe"] = (
        "needs consensus earnings estimates, which this system does not carry; "
        "deriving it from trailing earnings would be the trailing multiple renamed")
    return out, withheld


def cagr(points: list[tuple[str, float]]) -> float | None:
    """Compound annual growth between the first and last point, by calendar time."""
    if len(points) < 2:
        return None
    (start, first), (end, last) = points[0], points[-1]
    if first <= 0 or last <= 0:
        return None
    years = (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days / 365.25
    if years < 0.75:
        return None
    return (last / first) ** (1 / years) - 1


def historical_growth(f: Fundamentals) -> tuple[float | None, str]:
    """Realised revenue growth and a sentence saying where it came from."""
    points = f.history.get("revenue", []) if f.history else []
    g = cagr(points)
    if g is not None:
        return g, f"realised revenue CAGR of {g * 100:.1f}% over {len(points) - 1} years"
    if f.revenue and f.revenue_prior and f.revenue > 0 and f.revenue_prior > 0:
        g = f.revenue / f.revenue_prior - 1
        return g, f"one year of revenue growth, {g * 100:.1f}% — a thin anchor"
    return None, "no revenue history"


def cases(f: Fundamentals) -> list[dict]:
    """Bear, base and bull, anchored to the company's own measured history.

    The growth rates are the company's realised revenue CAGR and fractions of
    it, so a reader who thinks the past will not repeat can say so and move
    the number. With no history the cases spread around nominal GDP and say so.
    """
    realised, basis = historical_growth(f)
    if realised is None:
        anchor = GDP_ANCHOR
        basis = "no growth history, so a spread around long-run nominal GDP (4%)"
    else:
        anchor = realised
    capped = max(GROWTH_FLOOR, min(anchor, GROWTH_CAP))
    if capped > 0:
        bear, base, bull = capped / 3, capped * 0.6, capped
    else:
        # A shrinking company: the bull case is that the shrinking stops.
        bear, base, bull = capped, capped / 2, 0.0
    note = "; capped at 25%" if anchor > GROWTH_CAP else (
        "; floored at -10%" if anchor < GROWTH_FLOOR else "")
    return [
        {"name": "bear", "growth": _r(bear), "discount": 0.11, "terminal": 0.015,
         "reasoning": f"a third of the anchor, a higher discount rate. Anchor: {basis}{note}."},
        {"name": "base", "growth": _r(base), "discount": BASE_DISCOUNT,
         "terminal": BASE_TERMINAL,
         "reasoning": f"60% of the anchor, because growth decays. Anchor: {basis}{note}."},
        {"name": "bull", "growth": _r(bull), "discount": 0.08, "terminal": 0.030,
         "reasoning": f"the anchor sustained. Anchor: {basis}{note}."},
    ]


def project(base: float, growth: float, terminal: float,
            years: int = FORECAST_YEARS) -> list[float]:
    """Flows for each explicit year, growth fading linearly to the terminal rate.

    Linear fade rather than a step: a company growing 20% does not drop to 2.5%
    in one year, and a cliff mid-forecast is then capitalised by the terminal.
    """
    flows, value = [], base
    for year in range(years):
        share = year / (years - 1) if years > 1 else 1.0
        value *= 1 + growth + (terminal - growth) * share
        flows.append(value)
    return flows


def present_value(flows: list[float], discount: float,
                  terminal: float) -> tuple[float, float] | None:
    """(total present value, present value of the terminal), or None where the
    Gordon denominator approaches zero and the 'value' would be a division by
    almost nothing presented as a finding."""
    if discount - terminal < MIN_SPREAD:
        return None
    explicit = sum(flow / (1 + discount) ** t for t, flow in enumerate(flows, start=1))
    tail = flows[-1] * (1 + terminal) / (discount - terminal) / (1 + discount) ** len(flows)
    return explicit + tail, tail


def dcf(f: Fundamentals, price: float | None, growth: float) -> tuple[dict | None, str | None]:
    """Per-share value across the discount x terminal grid, at the base-case growth."""
    fcf, shares = free_cash_flow(f), _shares(f)
    if fcf is None or shares is None:
        return None, "needs operating cash flow, capex and a share count"
    if fcf <= 0:
        return None, ("free cash flow is not positive; a DCF on negative cash flow values "
                      "the company below nothing and says only that the flows have to turn")
    per_share = fcf / shares
    grid: list[list[float | None]] = []
    for r in DISCOUNT_RATES:
        row: list[float | None] = []
        for g in TERMINAL_RATES:
            pv = present_value(project(per_share, growth, g), r, g)
            row.append(None if pv is None else _r(pv[0], 2))
        grid.append(row)
    base_pv = present_value(project(per_share, growth, BASE_TERMINAL), BASE_DISCOUNT,
                            BASE_TERMINAL)
    share = None if base_pv is None or base_pv[0] <= 0 else base_pv[1] / base_pv[0]
    return {
        "base_fcf_per_share": _r(per_share, 4),
        "growth": _r(growth),
        "discount_rates": list(DISCOUNT_RATES),
        "terminal_growth": list(TERMINAL_RATES),
        "grid": grid,
        "terminal_share": _r(share),
        "price": _r(price, 2),
        "note": ("Terminal value is most of every figure here. The spread across the grid "
                 "is the honest width of the answer."),
    }, None


def _value_at_constant_growth(per_share: float, g: float) -> float:
    flows = [per_share * (1 + g) ** t for t in range(1, FORECAST_YEARS + 1)]
    pv = present_value(flows, BASE_DISCOUNT, BASE_TERMINAL)
    return pv[0] if pv else math.nan


def implied_growth(per_share: float, price: float, tol: float = 1e-6) -> float | None:
    """Bisect for the constant 10-year growth whose DCF equals `price`.

    Value rises monotonically with growth when the base flow is positive, so a
    bracketing search is exact to `tol` and cannot wander. None when the price
    lies outside what -50%..+100% a year can produce.
    """
    lo, hi = IMPLIED_LO, IMPLIED_HI
    if per_share <= 0 or price <= 0:
        return None
    if not _value_at_constant_growth(per_share, lo) <= price <= _value_at_constant_growth(
            per_share, hi):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if _value_at_constant_growth(per_share, mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def reverse_dcf(f: Fundamentals, price: float | None) -> dict | None:
    fcf, shares = free_cash_flow(f), _shares(f)
    if fcf is None or shares is None or fcf <= 0:
        return None
    hist, basis = historical_growth(f)
    out = {"implied_growth": None, "historical_growth": _r(hist), "discount": BASE_DISCOUNT,
           "terminal": BASE_TERMINAL, "years": FORECAST_YEARS}
    if price is None or price <= 0:
        out["reading"] = "needs a current price"
        return out
    g = implied_growth(fcf / shares, price)
    out["implied_growth"] = _r(g)
    if g is None:
        out["reading"] = ("the price lies outside what -50% to +100% a year of free-cash-flow "
                          "growth can produce at these rates")
        return out
    reading = (f"at {price:,.2f} the price implies free cash flow growing {g * 100:.1f}% a year "
               f"for {FORECAST_YEARS} years, then {BASE_TERMINAL * 100:.1f}% forever, "
               f"discounted at {BASE_DISCOUNT * 100:.0f}%")
    if hist is not None:
        gap = g - hist
        verdict = ("more than the company has delivered" if gap > 0.02 else
                   "less than the company has delivered" if gap < -0.02 else
                   "roughly what the company has delivered")
        reading += f" — {verdict} ({basis})"
    out["reading"] = reading
    return out


def build(f: Fundamentals, price: float | None) -> dict:
    """Multiples, three cases, the sensitivity grid and the reverse DCF."""
    mult, withheld = multiples(f, price)
    scenarios = cases(f)
    base_growth = next(c["growth"] for c in scenarios if c["name"] == "base")
    grid, why = dcf(f, price, base_growth)
    if why:
        withheld["dcf"] = why
        withheld["reverse_dcf"] = why
    fcf, shares = free_cash_flow(f), _shares(f)
    if fcf is not None and fcf > 0 and shares:
        per_share = fcf / shares
        for case in scenarios:
            pv = present_value(project(per_share, case["growth"], case["terminal"]),
                               case["discount"], case["terminal"])
            case["value_per_share"] = None if pv is None else _r(pv[0], 2)
    else:
        for case in scenarios:
            case["value_per_share"] = None
    return {"multiples": mult, "withheld": withheld, "cases": scenarios, "dcf": grid,
            "reverse_dcf": reverse_dcf(f, price)}
