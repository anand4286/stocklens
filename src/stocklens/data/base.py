"""The data contract every provider implements.

Analytics never import a provider. They receive these plain dataclasses, so a
new source (Polygon, IEX Cloud, a CSV in a notebook) is one adapter and no
change to the maths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

#: US equity tickers: letters, optionally a class suffix (BRK.B, BF-B).
#: Validated at the edge so a symbol can never carry a path, a query string
#: or a header into an upstream URL.
SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")


def normalise_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(f"not a valid ticker: {raw!r}")
    return symbol


@dataclass(frozen=True, slots=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class NewsItem:
    published: datetime
    headline: str
    source: str
    url: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class Fundamentals:
    """Latest annual figures from the filings, in USD. `None` means not reported.

    Absent is not zero: a company that does not report long-term debt has not
    told us it has none, and the scorecard treats the two differently.
    """

    company: str
    fiscal_year: int | None
    revenue: float | None = None
    revenue_prior: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    long_term_debt: float | None = None
    cash: float | None = None
    equity: float | None = None
    shares_outstanding: float | None = None
    source: str = "unknown"
    extra: dict[str, float] = field(default_factory=dict)
    # -- added for the research layer (forensics, valuation). Each defaults to
    # None so older adapters and hand-built test fixtures keep working: a field
    # nobody filled is unknown, never zero.
    interest_expense: float | None = None
    income_tax: float | None = None
    pretax_income: float | None = None
    depreciation: float | None = None
    stock_compensation: float | None = None
    research_development: float | None = None
    sga: float | None = None
    receivables: float | None = None
    inventory: float | None = None
    ppe_net: float | None = None
    retained_earnings: float | None = None
    #: Cash paid out, reported as positive amounts like capex.
    dividends_paid: float | None = None
    buybacks: float | None = None
    #: Weighted-average diluted shares for the fiscal year — the share count the
    #: per-share figures in the filing were divided by, unlike the point-in-time
    #: cover-page `shares_outstanding`.
    shares_diluted: float | None = None
    #: The prior fiscal year's value for any of the fields above, keyed by field
    #: name. Piotroski and Beneish are year-on-year comparisons and cannot run on
    #: a single year; a key that is absent or None means "not reported".
    prior: dict[str, float | None] = field(default_factory=dict)
    #: Up to five annual (period end ISO date, value) points, oldest first, for
    #: revenue, net_income, operating_cash_flow, capex and shares_diluted.
    history: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    #: Four-digit Standard Industrial Classification code from the filer's SEC
    #: profile. It decides which sector fund the factor attribution uses; None
    #: when the source does not carry it (it is not in XBRL company facts).
    sic: int | None = None


class PriceSource(Protocol):
    name: str

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        """Oldest first, split/dividend adjusted where the source supports it."""
        ...


class NewsSource(Protocol):
    name: str

    def news(self, symbol: str, limit: int) -> list[NewsItem]:
        """Newest first."""
        ...


class FundamentalsSource(Protocol):
    name: str

    def fundamentals(self, symbol: str) -> Fundamentals | None: ...


class DataUnavailable(RuntimeError):
    """The upstream had nothing for this symbol, or could not be reached."""
