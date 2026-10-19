"""Accounting forensics: four published models and a set of graded red flags.

Why published models rather than a house score
----------------------------------------------
A score someone invented last week can be tuned until it says what its author
hoped. Piotroski's F-score (2000), Altman's Z (1968, and the Z'' revision of
1995), Beneish's M-score (1999) and Sloan's accruals ratio (1996) each come with
fixed coefficients and fixed thresholds that were set out of sample, decades
ago, by people with no view on the company in front of you. Reproducing them
exactly is the point: a reader can check every figure against the paper.

Unknown is not fail
-------------------
Each model is a set of ratios, and any ratio may be uncomputable from a given
filing. A Piotroski test whose inputs are missing scores `passed=None` — not a
miss, which would punish thin filings, and not a pass, which would reward them.
`max_known` says how many of the nine could be tested, and with fewer than
seven the total is withheld: 3/4 and 3/9 are different findings and printing
"3" for both would erase the difference.

Graded, not binary
------------------
A red flag records the measured quantity, the threshold, and a severity on
0..1 from how far past the line the measurement sits (`severity_of`).
A cash conversion of 0.79 against a floor of 0.80 and one of 0.10
both fail; only one of them is a finding worth reading first.
"""

from __future__ import annotations

import math

from ..data.base import Fundamentals

# -- thresholds: every judgement call in one place ----------------------------

#: Operating cash flow over net income. Matches `analytics.signals`'
#: cash_conversion check (80%), so the two layers cannot disagree.
CASH_CONVERSION_FLOOR = 0.80
#: Growth in diluted shares year on year. Above 2% a holder is being diluted
#: faster than most buyback programmes could plausibly offset.
DILUTION_CEILING = 0.02
#: Stock compensation as a share of operating cash flow. Above a fifth, a large
#: part of the "cash" generated is cash not paid in wages because shares were.
SBC_OCF_CEILING = 0.20
#: Effective tax rate below which profit depends on a credit, a carried-forward
#: loss or a structure — well under the 21% US federal statutory rate.
TAX_RATE_FLOOR = 0.10
#: Operating profit over interest. Under 3x an ordinary bad year reaches the
#: lenders before it reaches the owners.
INTEREST_COVER_FLOOR = 3.0
#: Receivables (or inventory) growth relative to revenue growth, as an index
#: (1.0 = in step). Above 1.10, sales are being booked ahead of collection or
#: stock is building ahead of demand.
WORKING_CAPITAL_INDEX_CEILING = 1.10
#: Sloan accruals: (net income - operating cash flow) / average assets. Above
#: 10% of assets, earnings lean on accruals, which Sloan found reverse.
SLOAN_CEILING = 0.10
#: An accumulated deficit is raised at a fixed moderate weight: ordinary for a
#: young business, a finding for an established one; the reader places it.
ACCUMULATED_DEFICIT_SEVERITY = 0.5

#: Fewer testable Piotroski signals than this and the total is withheld.
PIOTROSKI_MIN_KNOWN = 7

#: Altman zones. Z (1968, public manufacturers) and Z'' (1995, non-manufacturers
#: and emerging markets — no sales term, book rather than market equity). Z'' is
#: the default here because the sector is not known and Z's sales/assets term
#: badly mis-scores asset-light businesses.
ALTMAN_Z_ZONES = {"distress_below": 1.81, "safe_above": 2.99}
ALTMAN_Z2_ZONES = {"distress_below": 1.10, "safe_above": 2.60}

#: Beneish. -1.78 is the 8-variable cut-off from the 1999 paper. The 5-variable
#: model (dropping SGAI, LVGI and TATA) has different coefficients and its own
#: published cut-off, -2.22; applying -1.78 to it would be a different test.
BENEISH_8_THRESHOLD = -1.78
BENEISH_5_THRESHOLD = -2.22

#: Severity floor and ceiling. A failure at all is a finding (floor above zero);
#: a breach by the threshold's own size again is as much as the measurement can
#: say, so it saturates there.
MIN_SEVERITY, MAX_SEVERITY = 0.25, 1.0
BREACH_AT_FULL_SEVERITY = 1.0


# -- helpers --------------------------------------------------------------------


def _r(x: float | None, n: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, n)


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    out = a / b
    return out if math.isfinite(out) else None


def _prior(f: Fundamentals, name: str) -> float | None:
    value = f.prior.get(name) if f.prior else None
    return None if value is None or not math.isfinite(value) else float(value)


def _avg(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def severity_of(breach: float | None) -> float:
    """Grade a failure by how far past its threshold it sits, on 0.25..1.

    Proportional to the breach *relative to the threshold*, so the scale means
    the same thing for a ratio, a percentage and a multiple.
    """
    if breach is None or not math.isfinite(breach):
        return MIN_SEVERITY
    share = min(BREACH_AT_FULL_SEVERITY, max(0.0, breach)) / BREACH_AT_FULL_SEVERITY
    return round(MIN_SEVERITY + share * (MAX_SEVERITY - MIN_SEVERITY), 2)


# -- Piotroski ------------------------------------------------------------------


def _ebit(f: Fundamentals, prior: bool = False) -> float | None:
    if prior:
        op = _prior(f, "operating_income")
        if op is not None:
            return op
        pre, interest = _prior(f, "pretax_income"), _prior(f, "interest_expense")
    else:
        if f.operating_income is not None:
            return f.operating_income
        pre, interest = f.pretax_income, f.interest_expense
    return None if pre is None else pre + (interest or 0.0)


def piotroski(f: Fundamentals) -> dict:
    """Piotroski (2000) F-score: nine binary tests of profitability, leverage
    and efficiency, each comparing this year with the last.

    ROA uses total assets at the start of the year, as in the paper; where the
    prior balance sheet is missing, the test is unknown rather than run on the
    year-end figure.
    """
    tests: list[dict] = []

    def add(name: str, rule: str, measured: float | None, passed: bool | None,
            detail: str) -> None:
        tests.append({"name": name, "rule": rule, "measured": _r(measured),
                      "passed": passed, "detail": detail})

    ta, ta_p = f.total_assets, _prior(f, "total_assets")
    roa = _div(f.net_income, ta_p)
    add("ROA positive", "net income / opening total assets > 0", roa,
        None if roa is None else roa > 0,
        "unknown: needs net income and last year's assets" if roa is None else f"ROA {_pct(roa)}")

    cfo = f.operating_cash_flow
    add("Operating cash flow positive", "operating cash flow > 0", cfo,
        None if cfo is None else cfo > 0,
        "unknown: operating cash flow not reported" if cfo is None
        else f"operating cash flow {cfo:,.0f}")

    # The balance sheet two years back is not carried, so the change in ROA is
    # measured on year-end assets for both years — consistent, if not the paper's.
    roa_now, roa_then = _div(f.net_income, ta), _div(_prior(f, "net_income"), ta_p)
    delta_roa = None if roa_now is None or roa_then is None else roa_now - roa_then
    add("ROA improving", "ROA this year > ROA last year (each on year-end assets)", delta_roa,
        None if delta_roa is None else delta_roa > 0,
        "unknown: needs both years' net income and assets" if delta_roa is None
        else f"ROA {_pct(roa_now)} vs {_pct(roa_then)}")

    cfo_ta = _div(cfo, ta_p)
    accrual = None if cfo_ta is None or roa is None else cfo_ta - roa
    add("Cash flow exceeds earnings", "operating cash flow / assets > ROA (low accruals)",
        accrual, None if accrual is None else accrual > 0,
        "unknown: needs cash flow, net income and assets" if accrual is None
        else f"cash flow return {_pct(cfo_ta)} vs ROA {_pct(roa)}")

    lev_now = _div(f.long_term_debt, ta)
    lev_then = _div(_prior(f, "long_term_debt"), ta_p)
    delta_lev = None if lev_now is None or lev_then is None else lev_now - lev_then
    add("Leverage falling", "long-term debt / total assets lower than last year", delta_lev,
        None if delta_lev is None else delta_lev < 0 or (lev_now == 0 and lev_then == 0),
        "unknown: needs both years' long-term debt and assets" if delta_lev is None
        else f"debt/assets {_pct(lev_now)} vs {_pct(lev_then)}")

    cr_now = _div(f.current_assets, f.current_liabilities)
    cr_then = _div(_prior(f, "current_assets"), _prior(f, "current_liabilities"))
    delta_cr = None if cr_now is None or cr_then is None else cr_now - cr_then
    add("Liquidity improving", "current ratio higher than last year", delta_cr,
        None if delta_cr is None else delta_cr > 0,
        "unknown: needs both years' current assets and liabilities" if delta_cr is None
        else f"current ratio {cr_now:.2f} vs {cr_then:.2f}")

    sh_now, sh_then = f.shares_diluted, _prior(f, "shares_diluted")
    dilution = None if sh_now is None or not sh_then else sh_now / sh_then - 1
    add("No new shares issued", "diluted share count not above last year", dilution,
        None if dilution is None else dilution <= 0,
        "unknown: needs both years' diluted share count" if dilution is None
        else f"diluted shares {'+' if dilution >= 0 else ''}{_pct(dilution)}")

    gm_now = _div(f.gross_profit, f.revenue)
    gm_then = _div(_prior(f, "gross_profit"), _prior(f, "revenue"))
    delta_gm = None if gm_now is None or gm_then is None else gm_now - gm_then
    add("Gross margin improving", "gross margin higher than last year", delta_gm,
        None if delta_gm is None else delta_gm > 0,
        "unknown: needs both years' gross profit and revenue" if delta_gm is None
        else f"gross margin {_pct(gm_now)} vs {_pct(gm_then)}")

    at_now = _div(f.revenue, ta)
    at_then = _div(_prior(f, "revenue"), ta_p)
    delta_at = None if at_now is None or at_then is None else at_now - at_then
    add("Asset turnover improving", "revenue / total assets higher than last year", delta_at,
        None if delta_at is None else delta_at > 0,
        "unknown: needs both years' revenue and assets" if delta_at is None
        else f"asset turnover {at_now:.2f} vs {at_then:.2f}")

    known = [t for t in tests if t["passed"] is not None]
    passes = sum(1 for t in known if t["passed"])
    if len(known) < PIOTROSKI_MIN_KNOWN:
        score = None
        reading = (f"only {len(known)} of 9 tests could run (at least {PIOTROSKI_MIN_KNOWN} "
                   f"needed) — withheld, which is not the same as weak")
    else:
        score = passes
        band = ("strong (8-9)" if passes >= 8 else "weak (0-2)" if passes <= 2
                else "middling (3-7)")
        reading = f"{passes} of {len(known)} testable signals pass — {band} on Piotroski's scale"
        if len(known) < 9:
            reading += f"; {9 - len(known)} could not be tested"
    return {"score": score, "max_known": len(known), "tests": tests, "reading": reading}


# -- Altman ---------------------------------------------------------------------


def _zone(z: float, zones: dict) -> str:
    if z > zones["safe_above"]:
        return "safe"
    if z < zones["distress_below"]:
        return "distress"
    return "grey"


def altman(f: Fundamentals, price: float | None) -> dict:
    """Altman Z'' (1995) by default, and the original 1968 Z where a market
    capitalisation and sales make it computable.

    Z   = 1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4(market equity / liabilities) + 1.0 X5
    Z'' = 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4(book equity / liabilities)

    X1 working capital / assets, X2 retained earnings / assets, X3 EBIT / assets,
    X5 sales / assets.
    """
    ta, tl = f.total_assets, f.total_liabilities
    wc = (None if f.current_assets is None or f.current_liabilities is None
          else f.current_assets - f.current_liabilities)
    x1, x2, x3 = _div(wc, ta), _div(f.retained_earnings, ta), _div(_ebit(f), ta)
    x5 = _div(f.revenue, ta)
    x4_book = _div(f.equity, tl)
    cap = (price * f.shares_outstanding
           if price and price > 0 and f.shares_outstanding and f.shares_outstanding > 0 else None)
    x4_mkt = _div(cap, tl)

    z2 = None
    if None not in (x1, x2, x3, x4_book):
        z2 = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4_book
    z1 = None
    if None not in (x1, x2, x3, x4_mkt, x5):
        z1 = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4_mkt + 1.0 * x5

    components = {"working_capital_to_assets": _r(x1), "retained_earnings_to_assets": _r(x2),
                  "ebit_to_assets": _r(x3), "book_equity_to_liabilities": _r(x4_book),
                  "market_equity_to_liabilities": _r(x4_mkt), "sales_to_assets": _r(x5)}
    original = {"z": _r(z1, 2), "zone": None if z1 is None else _zone(z1, ALTMAN_Z_ZONES),
                "thresholds": ALTMAN_Z_ZONES}

    if z2 is not None:
        z, variant, zones = z2, "Z''", ALTMAN_Z2_ZONES
    elif z1 is not None:
        z, variant, zones = z1, "Z", ALTMAN_Z_ZONES
    else:
        missing = [k for k, v in {"working capital": x1, "retained earnings": x2,
                                  "EBIT": x3, "equity and liabilities": x4_book}.items()
                   if v is None]
        return {"z": None, "variant": "Z''", "zone": None, "thresholds": ALTMAN_Z2_ZONES,
                "components": components, "original": original,
                "reading": "withheld — not computable without " + ", ".join(missing)}

    zone = _zone(z, zones)
    reading = (f"{variant} = {z:.2f}: {zone} zone (distress below "
               f"{zones['distress_below']}, safe above {zones['safe_above']})")
    if variant == "Z''" and z1 is not None:
        reading += f"; the 1968 manufacturing Z is {z1:.2f} ({original['zone']})"
    return {"z": _r(z, 2), "variant": variant, "zone": zone, "thresholds": zones,
            "components": components, "original": original, "reading": reading}


# -- Beneish --------------------------------------------------------------------


def beneish(f: Fundamentals) -> dict:
    """Beneish (1999) M-score: eight indices of how this year's accounts moved
    against last year's, weighted by a probit fitted on known manipulators.

    Above the threshold the pattern resembles the manipulators in the sample.
    It is a screen for accounts worth reading closely, not a finding of fraud.
    """
    def p(name: str) -> float | None:
        return _prior(f, name)

    rev, rev_p = f.revenue, p("revenue")
    dsri = _div(_div(f.receivables, rev), _div(p("receivables"), rev_p))
    gm, gm_p = _div(f.gross_profit, rev), _div(p("gross_profit"), rev_p)
    gmi = _div(gm_p, gm)

    def hard_assets_share(ca, ppe, ta):
        if ca is None or ppe is None or not ta:
            return None
        return 1 - (ca + ppe) / ta

    aqi = _div(hard_assets_share(f.current_assets, f.ppe_net, f.total_assets),
               hard_assets_share(p("current_assets"), p("ppe_net"), p("total_assets")))
    sgi = _div(rev, rev_p)

    def dep_rate(dep, ppe):
        return None if dep is None or ppe is None else _div(dep, dep + ppe)

    depi = _div(dep_rate(p("depreciation"), p("ppe_net")), dep_rate(f.depreciation, f.ppe_net))
    sgai = _div(_div(f.sga, rev), _div(p("sga"), rev_p))

    def leverage(cl, ltd, ta):
        return None if cl is None or ta is None else _div(cl + (ltd or 0.0), ta)

    lvgi = _div(leverage(f.current_liabilities, f.long_term_debt, f.total_assets),
                leverage(p("current_liabilities"), p("long_term_debt"), p("total_assets")))
    tata = (None if f.net_income is None or f.operating_cash_flow is None
            else _div(f.net_income - f.operating_cash_flow, f.total_assets))

    comps = {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi, "DEPI": depi,
             "SGAI": sgai, "LVGI": lvgi, "TATA": tata}
    components = {k: _r(v) for k, v in comps.items()}
    five = ("DSRI", "GMI", "AQI", "SGI", "DEPI")

    if all(v is not None for v in comps.values()):
        m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi
             - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
        model, threshold, note = "8-variable", BENEISH_8_THRESHOLD, ""
    elif all(comps[k] is not None for k in five):
        m = -6.065 + 0.823 * dsri + 0.906 * gmi + 0.593 * aqi + 0.717 * sgi + 0.107 * depi
        missing = [k for k in ("SGAI", "LVGI", "TATA") if comps[k] is None]
        model, threshold = "5-variable", BENEISH_5_THRESHOLD
        note = (f" — the 5-variable model, because {', '.join(missing)} could not be "
                f"computed; its own cut-off is {BENEISH_5_THRESHOLD}")
    else:
        missing = [k for k in five if comps[k] is None]
        return {"m": None, "model": None, "threshold": BENEISH_8_THRESHOLD, "flagged": None,
                "components": components,
                "reading": "withheld — even the 5-variable model needs " + ", ".join(missing)}

    flagged = m > threshold
    reading = (f"M = {m:.2f} against {threshold}{note}: "
               + ("resembles the manipulator sample — read the accounts closely" if flagged
                  else "does not resemble the manipulator sample"))
    return {"m": _r(m, 2), "model": model, "threshold": threshold, "flagged": flagged,
            "components": components, "reading": reading}


# -- Sloan ----------------------------------------------------------------------


def accruals(f: Fundamentals) -> dict:
    """Sloan (1996): the share of earnings that is accrual rather than cash,
    scaled by average total assets. High accruals predict earnings that fall."""
    ratio = None
    if f.net_income is not None and f.operating_cash_flow is not None:
        ratio = _div(f.net_income - f.operating_cash_flow,
                     _avg(f.total_assets, _prior(f, "total_assets")))
    rule = (f"(net income - operating cash flow) / average total assets; above "
            f"{_pct(SLOAN_CEILING)} earnings lean on accruals, which tend to reverse")
    return {"sloan_ratio": _r(ratio), "rule": rule,
            "raised": None if ratio is None else ratio > SLOAN_CEILING}


# -- red flags ------------------------------------------------------------------


def _flag(name: str, rule: str, measured: float | None, threshold: float | None,
          raised: bool | None, breach: float | None = None,
          severity: float | None = None) -> dict:
    if raised is None:
        sev = None
    elif not raised:
        sev = 0.0
    else:
        sev = severity if severity is not None else severity_of(breach)
    return {"name": name, "rule": rule, "measured": _r(measured), "threshold": threshold,
            "severity": sev, "raised": raised}


def red_flags(f: Fundamentals) -> list[dict]:
    """Eight graded concerns. `raised=None` means the inputs were not filed."""
    flags: list[dict] = []

    ni, ocf = f.net_income, f.operating_cash_flow
    conv = _div(ocf, ni) if ni is not None and ni > 0 else None
    flags.append(_flag(
        "Cash conversion", f"operating cash flow / net income at least "
        f"{CASH_CONVERSION_FLOOR:.2f} (needs positive net income)", conv, CASH_CONVERSION_FLOOR,
        None if conv is None else conv < CASH_CONVERSION_FLOOR,
        None if conv is None else (CASH_CONVERSION_FLOOR - conv) / CASH_CONVERSION_FLOOR))

    sh, sh_p = f.shares_diluted, _prior(f, "shares_diluted")
    dil = None if sh is None or not sh_p else sh / sh_p - 1
    flags.append(_flag(
        "Dilution", f"diluted shares grow no more than {_pct(DILUTION_CEILING)} a year",
        dil, DILUTION_CEILING, None if dil is None else dil > DILUTION_CEILING,
        None if dil is None else (dil - DILUTION_CEILING) / DILUTION_CEILING))

    sbc = f.stock_compensation
    share = _div(sbc, ocf) if sbc is not None and ocf is not None and ocf > 0 else None
    flags.append(_flag(
        "Stock compensation share of cash flow",
        f"stock compensation at most {_pct(SBC_OCF_CEILING)} of operating cash flow",
        share, SBC_OCF_CEILING, None if share is None else share > SBC_OCF_CEILING,
        None if share is None else (share - SBC_OCF_CEILING) / SBC_OCF_CEILING))

    tax, pre = f.income_tax, f.pretax_income
    rate = _div(tax, pre) if tax is not None and pre is not None and pre > 0 else None
    flags.append(_flag(
        "Tax rate anomaly", f"effective tax rate at least {_pct(TAX_RATE_FLOOR)} "
        "(21% federal statutory; needs positive pre-tax profit)", rate, TAX_RATE_FLOOR,
        None if rate is None else rate < TAX_RATE_FLOOR,
        None if rate is None else (TAX_RATE_FLOOR - rate) / TAX_RATE_FLOOR))

    re_ = f.retained_earnings
    flags.append(_flag(
        "Accumulated deficit", "retained earnings not negative — the company has made "
        "money in total over its life", re_, 0.0, None if re_ is None else re_ < 0,
        severity=ACCUMULATED_DEFICIT_SEVERITY))

    ebit, interest = _ebit(f), f.interest_expense
    if ebit is None or interest is None:
        cover, raised = None, None
    elif interest <= 0:
        cover, raised = None, False  # no interest to cover: the test passes, nothing to measure
    else:
        cover = ebit / interest
        raised = cover < INTEREST_COVER_FLOOR
    flags.append(_flag(
        "Interest cover", f"operating profit at least {INTEREST_COVER_FLOOR:.0f}x interest "
        "expense", cover, INTEREST_COVER_FLOOR, raised,
        None if cover is None else (INTEREST_COVER_FLOOR - cover) / INTEREST_COVER_FLOOR))

    sales_ratio = _div(f.revenue, _prior(f, "revenue"))
    for field, label in (("receivables", "Receivables"), ("inventory", "Inventory")):
        now, then = getattr(f, field), _prior(f, field)
        idx = None
        if sales_ratio and sales_ratio > 0 and now is not None and then and then > 0:
            idx = _div(now / then, sales_ratio)
        ceiling = WORKING_CAPITAL_INDEX_CEILING
        flags.append(_flag(
            f"{label} outpacing revenue",
            f"{label.lower()} growth / revenue growth (as ratios) at most {ceiling:.2f}",
            idx, ceiling, None if idx is None else idx > ceiling,
            None if idx is None else (idx - ceiling) / ceiling))
    return flags


def examine(f: Fundamentals, price: float | None) -> dict:
    """Every forensic model and flag for one filing."""
    return {"piotroski": piotroski(f), "altman": altman(f, price), "beneish": beneish(f),
            "accruals": accruals(f), "red_flags": red_flags(f)}
