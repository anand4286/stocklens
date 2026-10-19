"""SEC EDGAR XBRL company facts — free, authoritative fundamentals.

The SEC requires a descriptive User-Agent with a contact address and caps
clients at 10 requests a second. Both are respected: the agent string comes
from configuration, and responses are cached so a page view costs at most
one request per company per TTL.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import date

import httpx

from .base import DataUnavailable, Fundamentals

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
#: The filer's profile. Company facts carry no industry code; this does.
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)

#: Each field is the first tag that reports it. Companies choose among
#: equivalent tags, so revenue alone has three common spellings.
TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "equity": ("StockholdersEquity",),
    # -- the research layer's inputs; spellings follow the tags filers actually
    # use, most common first.
    "interest_expense": ("InterestExpense", "InterestExpenseDebt",
                         "InterestExpenseNonoperating"),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
    ),
    "depreciation": ("DepreciationDepletionAndAmortization",
                     "DepreciationAmortizationAndAccretionNet",
                     "DepreciationAndAmortization", "Depreciation"),
    "stock_compensation": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    "research_development": ("ResearchAndDevelopmentExpense",),
    "sga": ("SellingGeneralAndAdministrativeExpense",),
    "receivables": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "inventory": ("InventoryNet", "InventoryGross"),
    "ppe_net": ("PropertyPlantAndEquipmentNet",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "dividends_paid": ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"),
    "buybacks": ("PaymentsForRepurchaseOfCommonStock",),
    "shares_diluted": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
#: Flow items are summed over a period; balances are a point in time.
FLOWS = {"revenue", "gross_profit", "operating_income", "net_income",
         "operating_cash_flow", "capex", "interest_expense", "income_tax", "pretax_income",
         "depreciation", "stock_compensation", "research_development", "sga",
         "dividends_paid", "buybacks", "shares_diluted"}
#: XBRL reports most facts in USD; share counts are in `shares`.
UNITS = {"shares_diluted": "shares"}
#: Fields whose multi-year series the valuation layer needs for growth and a
#: DCF base. Five points is four year-on-year changes: enough to see a trend,
#: few enough that the oldest still describes the same company.
HISTORY_FIELDS = ("revenue", "net_income", "operating_cash_flow", "capex", "shares_diluted")
HISTORY_POINTS = 5
#: A "prior year" is a period end 10-14 months before the latest one. Anything
#: else is a gap in the filings, and comparing across a gap would report a
#: two-year change as a one-year change.
PRIOR_WINDOW_DAYS = (300, 430)


class EdgarFundamentals:
    name = "sec-edgar"

    def __init__(self, user_agent: str, ttl_seconds: int = 6 * 3600) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC requires a User-Agent with a contact email")
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip"}
        self._ttl = ttl_seconds
        self._ciks: dict[str, int] | None = None
        self._cache: dict[str, tuple[float, Fundamentals | None]] = {}
        self._lock = threading.Lock()

    def _get(self, url: str) -> dict:
        try:
            response = httpx.get(url, headers=self._headers, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise DataUnavailable(f"sec unreachable: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise DataUnavailable("sec has no filing data for this company")
        if response.status_code >= 400:
            raise DataUnavailable(f"sec returned HTTP {response.status_code}")
        return response.json()

    def _cik(self, symbol: str) -> int | None:
        with self._lock:
            if self._ciks is None:
                rows = self._get(TICKERS_URL).values()
                self._ciks = {r["ticker"].upper(): int(r["cik_str"]) for r in rows}
        return self._ciks.get(symbol.replace(".", "-"))

    def fundamentals(self, symbol: str) -> Fundamentals | None:
        now = time.monotonic()
        cached = self._cache.get(symbol)
        if cached and now - cached[0] < self._ttl:
            return cached[1]
        cik = self._cik(symbol)
        result = None if cik is None else parse_company_facts(self._get(FACTS_URL.format(cik=cik)))
        if result is not None and cik is not None:
            sic = self._sic(cik)
            if sic is not None:
                result = dataclasses.replace(result, sic=sic)
        self._cache[symbol] = (now, result)
        return result

    def _sic(self, cik: int) -> int | None:
        """The industry code, or None on any failure.

        A second request for a secondary field: the fundamentals are complete
        without it, so an outage or an odd payload here must never cost the
        page its figures. It shares the fundamentals' cache entry and so costs
        at most one extra request per company per TTL.
        """
        try:
            return parse_sic(self._get(SUBMISSIONS_URL.format(cik=cik)))
        except (DataUnavailable, ValueError):
            return None


def parse_sic(payload: dict) -> int | None:
    """`sicCode` from a submissions payload. The SEC sends it as a string."""
    raw = payload.get("sicCode") if isinstance(payload, dict) else None
    try:
        sic = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return sic if 0 < sic < 10000 else None


def _annual(facts: dict, tag: str, unit: str = "USD") -> list[dict]:
    """Every full-year 10-K row for a tag, oldest filing first.

    Grouping by `fy` is the trap here: `fy` is the fiscal year of the *filing*,
    and one 10-K restates two or three prior years under the same `fy`. The
    period is identified by its `end` date, which `_latest_by_end` keys on.
    """
    rows = facts.get(tag, {}).get("units", {}).get(unit, [])
    annual = [r for r in rows if r.get("form") == "10-K" and r.get("fp") == "FY" and "end" in r]
    return sorted(annual, key=lambda r: r.get("filed", ""))


def _latest_by_end(rows: list[dict], flow: bool) -> list[tuple[str, float]]:
    """(period end, value) pairs, oldest first; the latest filing wins a tie."""
    seen: dict[str, float] = {}
    for row in rows:
        if flow:
            if not row.get("start"):
                continue
            # A 10-K also carries the fourth quarter on its own; keep 12-month spans.
            span = (int(row["end"][:4]) - int(row["start"][:4])) * 12 + (
                int(row["end"][5:7]) - int(row["start"][5:7]))
            if span < 11:
                continue
        seen[row["end"]] = float(row["val"])
    return sorted(seen.items())


def _prior_point(series: list[tuple[str, float]]) -> float | None:
    """The value roughly one fiscal year before the latest point, or None."""
    if len(series) < 2:
        return None
    latest = date.fromisoformat(series[-1][0][:10])
    lo, hi = PRIOR_WINDOW_DAYS
    for end, value in reversed(series[:-1]):
        gap = (latest - date.fromisoformat(end[:10])).days
        if lo <= gap <= hi:
            return value
        if gap > hi:
            break
    return None


def parse_company_facts(payload: dict) -> Fundamentals | None:
    gaap = payload.get("facts", {}).get("us-gaap", {})
    if not gaap:
        return None
    values: dict[str, float | None] = {}
    prior: dict[str, float | None] = {}
    history: dict[str, list[tuple[str, float]]] = {}
    revenue_series: list[tuple[str, float]] = []
    fiscal_year: int | None = None
    for field, tags in TAGS.items():
        # Companies switch tags over time (Apple left `Revenues` in 2018), so
        # take whichever equivalent tag reports the most recent period.
        unit = UNITS.get(field, "USD")
        candidates = [_latest_by_end(_annual(gaap, tag, unit), flow=field in FLOWS)
                      for tag in tags]
        series = max((c for c in candidates if c), key=lambda c: c[-1][0], default=[])
        values[field] = series[-1][1] if series else None
        prior[field] = _prior_point(series)
        if field in HISTORY_FIELDS and series:
            history[field] = series[-HISTORY_POINTS:]
        if field == "revenue" and series:
            revenue_series = series
            fiscal_year = int(series[-1][0][:4])

    shares = None
    dei = payload.get("facts", {}).get("dei", {})
    share_rows = dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get(
        "shares", [])
    if share_rows:
        shares = float(max(share_rows, key=lambda r: r.get("end", ""))["val"])

    return Fundamentals(
        company=str(payload.get("entityName") or "unknown"),
        fiscal_year=fiscal_year,
        revenue_prior=revenue_series[-2][1] if len(revenue_series) >= 2 else None,
        shares_outstanding=shares,
        source="sec-edgar",
        prior=prior,
        history=history,
        **values,
    )
