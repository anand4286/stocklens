"""Deterministic synthetic market data.

The showcase deployment runs on this: it needs no keys, makes no outbound
calls, and cannot leak anything about a real account or a real position.
Every symbol gets its own stable series — the same ticker always draws the
same path — so links are shareable and tests are exact.

The generator is a GARCH-flavoured random walk with a per-symbol drift and
volatility, so the risk and technical panels have something realistic to say:
volatility clusters, drawdowns happen, and trends persist for a while.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, date, datetime, timedelta

from .base import Bar, Fundamentals, NewsItem

_ANCHOR = date(2026, 1, 2)


def _rng(symbol: str, salt: str) -> random.Random:
    digest = hashlib.sha256(f"{symbol}:{salt}".encode()).digest()
    # Not cryptographic: a reproducible seed for synthetic data.
    return random.Random(int.from_bytes(digest[:8], "big"))  # noqa: S311  # nosec B311


def _business_days(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


class DemoPrices:
    name = "demo"

    def __init__(self, today: date | None = None) -> None:
        self._today = today

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        end = self._today or max(_ANCHOR, date.today())
        rng = _rng(symbol, "prices")
        calendar = _business_days(end, days)

        # Per-symbol character: annual drift -10%..+25%, vol 18%..65%.
        drift = rng.uniform(-0.10, 0.25) / 252
        base_vol = rng.uniform(0.18, 0.65) / math.sqrt(252)
        price = rng.uniform(15, 450)
        avg_volume = rng.uniform(0.8e6, 40e6)

        # GARCH(1,1)-style variance so calm and turbulent stretches alternate.
        omega, alpha, beta = base_vol**2 * 0.05, 0.08, 0.87
        variance = base_vol**2
        shock = 0.0
        bars: list[Bar] = []
        for day in calendar:
            variance = omega + alpha * shock**2 + beta * variance
            sigma = math.sqrt(variance)
            shock = rng.gauss(0, sigma)
            ret = drift + shock
            open_ = price * (1 + rng.gauss(0, sigma / 3))
            close = max(1.0, price * math.exp(ret))
            span = abs(rng.gauss(0, sigma)) * price
            high = max(open_, close) + span / 2
            low = max(0.5, min(open_, close) - span / 2)
            volume = avg_volume * math.exp(rng.gauss(0, 0.35)) * (1 + 8 * abs(shock))
            bars.append(Bar(day, round(open_, 2), round(high, 2), round(low, 2),
                            round(close, 2), round(volume)))
            price = close
        return bars


_POSITIVE = [
    "{c} beats quarterly revenue estimates as demand holds up",
    "Analysts raise price targets on {c} after strong guidance",
    "{c} announces expanded buyback programme",
    "{c} wins multi-year contract with major customer",
]
_NEGATIVE = [
    "{c} shares slip as margins come under pressure",
    "{c} cuts full-year outlook citing softer orders",
    "Regulators open inquiry into {c} disclosures",
    "{c} faces downgrade on valuation concerns",
]
_NEUTRAL = [
    "{c} to present at upcoming industry conference",
    "{c} schedules quarterly earnings call",
    "{c} names new chief financial officer",
    "What to watch in {c} options this week",
]


class DemoNews:
    name = "demo"

    def news(self, symbol: str, limit: int) -> list[NewsItem]:
        rng = _rng(symbol, "news")
        tilt = rng.uniform(-1, 1)
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        items: list[NewsItem] = []
        for i in range(min(limit, 12)):
            roll = rng.random() + tilt * 0.25
            pool = _POSITIVE if roll > 0.66 else _NEGATIVE if roll < 0.33 else _NEUTRAL
            headline = rng.choice(pool).format(c=symbol)
            items.append(NewsItem(
                published=now - timedelta(hours=7 * i + rng.randint(0, 6)),
                headline=headline,
                source="Demo Wire",
                url=None,
                summary="Synthetic headline generated for demo mode.",
            ))
        return items


class DemoFundamentals:
    name = "demo"

    def fundamentals(self, symbol: str) -> Fundamentals:
        rng = _rng(symbol, "fundamentals")
        revenue = rng.uniform(0.5e9, 120e9)
        growth = rng.uniform(-0.12, 0.35)
        gross = revenue * rng.uniform(0.2, 0.75)
        operating = gross * rng.uniform(-0.2, 0.6)
        net = operating * rng.uniform(0.55, 0.85)
        assets = revenue * rng.uniform(0.8, 2.5)
        liabilities = assets * rng.uniform(0.25, 0.85)
        current_liabilities = liabilities * rng.uniform(0.2, 0.5)
        return Fundamentals(
            company=f"{symbol} Holdings (demo)",
            fiscal_year=_ANCHOR.year - 1,
            revenue=revenue,
            revenue_prior=revenue / (1 + growth),
            gross_profit=gross,
            operating_income=operating,
            net_income=net,
            operating_cash_flow=net * rng.uniform(0.8, 1.5),
            capex=revenue * rng.uniform(0.02, 0.12),
            total_assets=assets,
            total_liabilities=liabilities,
            current_assets=current_liabilities * rng.uniform(0.7, 2.6),
            current_liabilities=current_liabilities,
            long_term_debt=liabilities * rng.uniform(0.1, 0.6),
            cash=assets * rng.uniform(0.03, 0.25),
            equity=assets - liabilities,
            shares_outstanding=rng.uniform(0.1e9, 5e9),
            source="demo",
        )
