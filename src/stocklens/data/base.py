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
