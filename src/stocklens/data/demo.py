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
from functools import lru_cache

from .base import Bar, Fundamentals, NewsItem
from .sectors import ETF_SECTORS, OVERRIDES, REPRESENTATIVE_SIC, SECTOR_ETFS

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


#: Every series is generated over this many sessions and then cut to the
#: request, so asking for 300 days returns the tail of the 756-day series
#: rather than a different path.
_GENERATED = 1260
#: The eleven SPDR sectors, in a fixed order so the hash below is stable.
_SECTORS = tuple(SECTOR_ETFS)
#: Factor vols, annualised. The market factor is what makes a basket of demo
#: names look like a real one: one eigenvalue well above the noise band.
_MARKET_VOL, _SECTOR_VOL, _IDIO_FLOOR = 0.16, 0.12, 0.08
BENCHMARKS = {"SPY", "VOO", "IVV"}


#: Market beta of each demo sector fund. Roughly the real ones' long-run
#: values: utilities and staples are defensive, technology and discretionary
#: amplify the market. Without this spread every sector would look alike.
_ETF_MARKET_BETA = {
    "XLK": 1.15, "XLF": 1.10, "XLE": 0.90, "XLV": 0.75, "XLY": 1.15, "XLP": 0.60,
    "XLI": 1.05, "XLB": 1.00, "XLU": 0.50, "XLRE": 0.85, "XLC": 1.05,
}
#: A sector fund holds dozens of names, so almost all of its idiosyncratic
#: risk diversifies away; what is left is a few percent a year.
_ETF_IDIO = 0.03


def sector_of(symbol: str) -> str:
    """The demo sector: the curated one for well-known names, else a stable hash.

    A sector fund is in its own sector. The hash spreads every other ticker
    over the eleven SPDR sectors, so a demo basket has sector clusters that the
    attribution regressions can find against the matching demo fund.
    """
    known = OVERRIDES.get(symbol) or ETF_SECTORS.get(symbol)
    if known:
        return known
    return _SECTORS[hashlib.sha256(symbol.encode()).digest()[0] % len(_SECTORS)]


@lru_cache(maxsize=32)
def _factor(name: str, end: date, count: int) -> tuple[float, ...]:
    """A shared daily shock series, GARCH-clustered, the same for every symbol."""
    rng = _rng(name, "factor")
    daily = (_MARKET_VOL if name == "market" else _SECTOR_VOL) / math.sqrt(252)
    omega, alpha, beta = daily**2 * 0.05, 0.08, 0.87
    variance, shock, out = daily**2, 0.0, []
    for _ in range(count):
        variance = omega + alpha * shock**2 + beta * variance
        shock = rng.gauss(0, math.sqrt(variance))
        out.append(shock)
    return tuple(out)


class DemoPrices:
    """Market factor + sector factor + GARCH idiosyncratic noise, per symbol.

    Each symbol's own variance is split: beta to the market, a loading on its
    sector, and whatever is left as idiosyncratic. So correlations in a demo
    basket are positive and clustered by sector, as they are in a real one —
    while the expected return stays a random walk with drift, so no signal
    can find an edge that is not there.
    """

    name = "demo"

    def __init__(self, today: date | None = None) -> None:
        self._today = today

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        end = self._today or max(_ANCHOR, date.today())
        count = max(days, _GENERATED)
        rng = _rng(symbol, "prices")
        calendar = _business_days(end, count)

        # Per-symbol character: annual drift -10%..+25%, vol 18%..65%.
        drift = rng.uniform(-0.10, 0.25) / 252
        base_vol = rng.uniform(0.18, 0.65) / math.sqrt(252)
        price = rng.uniform(15, 450)
        avg_volume = rng.uniform(0.8e6, 40e6)

        loadings = _rng(symbol, "loadings")
        market = _factor("market", end, count)
        if symbol in BENCHMARKS:
            mkt_beta, sec_load, sector = 1.0, 0.0, None
            idio_vol = 0.01 / math.sqrt(252)
        elif symbol in ETF_SECTORS:
            # A sector fund is the market plus its sector, almost nothing else.
            mkt_beta, sec_load = _ETF_MARKET_BETA[symbol], 1.0
            sector = _factor(ETF_SECTORS[symbol], end, count)
            idio_vol = _ETF_IDIO / math.sqrt(252)
            drift = drift * 0.4  # a basket's drift is narrower than one stock's
        else:
            mkt_beta, sec_load = loadings.uniform(0.6, 1.5), loadings.uniform(0.4, 1.0)
            sector = _factor(sector_of(symbol), end, count)
            systematic = (mkt_beta * _MARKET_VOL) ** 2 + (sec_load * _SECTOR_VOL) ** 2
            idio_annual = math.sqrt(max(base_vol**2 * 252 - systematic, _IDIO_FLOOR**2))
            idio_vol = idio_annual / math.sqrt(252)

        # GARCH(1,1)-style idiosyncratic variance so calm and turbulent stretches alternate.
        omega, alpha, beta = idio_vol**2 * 0.05, 0.08, 0.87
        variance = idio_vol**2
        shock = 0.0
        bars: list[Bar] = []
        for i, day in enumerate(calendar):
            variance = omega + alpha * shock**2 + beta * variance
            shock = rng.gauss(0, math.sqrt(variance))
            common = mkt_beta * market[i] + (sec_load * sector[i] if sector else 0.0)
            total = shock + common
            sigma = math.sqrt(variance + common**2)
            ret = drift + total
            open_ = price * (1 + rng.gauss(0, sigma / 3))
            close = max(1.0, price * math.exp(ret))
            span = abs(rng.gauss(0, sigma)) * price
            high = max(open_, close) + span / 2
            low = max(0.5, min(open_, close) - span / 2)
            volume = avg_volume * math.exp(rng.gauss(0, 0.35)) * (1 + 8 * abs(total))
            bars.append(Bar(day, round(open_, 2), round(high, 2), round(low, 2),
                            round(close, 2), round(volume)))
            price = close
        return bars[-days:]


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
    """Synthetic filings that hang together.

    The headline lines are drawn first, exactly as before, so every existing
    demo page keeps its numbers. The research layer's inputs are drawn from a
    second, independent stream and *derived* from those lines — opex is what
    separates gross from operating profit, pre-tax profit is net income grossed
    up by a tax rate — so Piotroski, Altman and Beneish have something coherent
    to disagree about rather than unrelated noise.

    Each ticker also draws a "temperament" that tilts the year-on-year changes
    (receivables outpacing sales, a share count creeping up), so across a
    handful of tickers some scores pass and some fail.
    """

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
        ocf = net * rng.uniform(0.8, 1.5)
        capex = revenue * rng.uniform(0.02, 0.12)
        current_assets = current_liabilities * rng.uniform(0.7, 2.6)
        long_term_debt = liabilities * rng.uniform(0.1, 0.6)
        cash = assets * rng.uniform(0.03, 0.25)
        rng.uniform(0.1e9, 5e9)  # the old share-count draw, kept so later draws do not move
        revenue_prior = revenue / (1 + growth)

        # Shares are set from the demo price, so multiples land where real ones
        # do: a trailing P/E of 12-40x for a profitable company, 0.5-3x sales
        # for one losing money. An independent share count gave P/E of 3x.
        v = _rng(symbol, "valuation")
        cap = net * v.uniform(12, 40) if net > 0 else revenue * v.uniform(0.5, 3.0)
        cap = max(cap, revenue * 0.3)
        price = DemoPrices().daily_bars(symbol, 1)[-1].close
        shares = cap / price

        d = _rng(symbol, "fundamentals-detail")
        # -1 is aggressive accounting and loose capital discipline, +1 the reverse.
        temper = d.uniform(-1, 1)

        def drift(centre: float, spread: float) -> float:
            """A year-on-year ratio, tilted by temperament."""
            return 1 + centre + d.gauss(0, spread)

        opex = gross - operating
        sga = opex * d.uniform(0.45, 0.75)
        rnd = (opex - sga) * d.uniform(0.0, 0.9)
        tax_rate = d.uniform(0.02, 0.28)
        pretax = net / (1 - tax_rate) if net > 0 else net
        income_tax = pretax - net
        ppe = assets * d.uniform(0.1, 0.45)
        depreciation = ppe * d.uniform(0.07, 0.18)
        receivables = revenue * d.uniform(0.06, 0.2)
        inventory = revenue * d.uniform(0.0, 0.15)
        retained = (assets - liabilities) * d.uniform(-0.6, 1.3)
        dilution = d.uniform(-0.04, 0.03) - 0.03 * temper
        shares_diluted = shares * d.uniform(1.0, 1.03)
        payout = max(net, 0) * d.uniform(0.0, 0.45)
        buybacks = max(ocf - capex, 0) * d.uniform(0.0, 0.6)

        current = {
            "revenue": revenue, "gross_profit": gross, "operating_income": operating,
            "net_income": net, "operating_cash_flow": ocf, "capex": capex,
            "total_assets": assets, "total_liabilities": liabilities,
            "current_assets": current_assets, "current_liabilities": current_liabilities,
            "long_term_debt": long_term_debt, "cash": cash, "equity": assets - liabilities,
            "interest_expense": long_term_debt * d.uniform(0.025, 0.07),
            "income_tax": income_tax, "pretax_income": pretax,
            "depreciation": depreciation, "stock_compensation": revenue * d.uniform(0.004, 0.1),
            "research_development": rnd, "sga": sga, "receivables": receivables,
            "inventory": inventory, "ppe_net": ppe, "retained_earnings": retained,
            "dividends_paid": payout, "buybacks": buybacks, "shares_diluted": shares_diluted,
        }

        # The prior year: revenue is fixed by `revenue_prior`; everything else
        # moves with the business plus noise, and the temperament decides
        # whether receivables, inventory and leverage run ahead of sales.
        sales_ratio = 1 + growth
        balance_ratio = 1 + growth * d.uniform(0.3, 0.9)
        prior_gm = gross / revenue - d.gauss(0.005 * temper, 0.02)
        prior_margin = net / revenue - d.gauss(0.01 * temper, 0.025)
        prior = {
            "revenue": revenue_prior,
            "gross_profit": revenue_prior * prior_gm,
            "operating_income": operating / sales_ratio * drift(0, 0.08),
            "net_income": revenue_prior * prior_margin,
            "operating_cash_flow": ocf / sales_ratio * drift(0, 0.1),
            "capex": capex / sales_ratio * drift(0, 0.1),
            "total_assets": assets / balance_ratio,
            "total_liabilities": liabilities / balance_ratio / drift(-0.03 * temper, 0.04),
            "current_assets": current_assets / balance_ratio / drift(0.02 * temper, 0.05),
            "current_liabilities": current_liabilities / balance_ratio,
            "long_term_debt": long_term_debt / drift(-0.05 * temper, 0.08),
            "cash": cash / drift(0, 0.15),
            "interest_expense": current["interest_expense"] / drift(0, 0.08),
            "income_tax": income_tax / sales_ratio * drift(0, 0.1),
            "pretax_income": pretax / sales_ratio * drift(0, 0.08),
            "depreciation": depreciation / balance_ratio * drift(0, 0.05),
            "stock_compensation": current["stock_compensation"] / sales_ratio,
            "research_development": rnd / sales_ratio,
            "sga": sga / sales_ratio * drift(0.02 * temper, 0.04),
            # Receivables and inventory are the tell: a negative temperament
            # grows them faster than sales.
            "receivables": receivables / sales_ratio / drift(-0.08 * temper, 0.05),
            "inventory": inventory / sales_ratio / drift(-0.06 * temper, 0.05),
            "ppe_net": ppe / balance_ratio,
            "retained_earnings": retained - net + payout,
            "dividends_paid": payout / drift(0.03, 0.05),
            "buybacks": buybacks / drift(0, 0.2),
            "shares_diluted": shares_diluted / (1 + dilution),
        }
        prior["equity"] = prior["total_assets"] - prior["total_liabilities"]

        # Five fiscal years, oldest first, ending in the two years above.
        years = [_ANCHOR.year - 5 + i for i in range(5)]
        ends = [f"{y}-12-31" for y in years]
        rev = [revenue_prior, revenue]
        for _ in range(3):
            rev.insert(0, rev[0] / (1 + growth + d.gauss(0, 0.05)))
        margin = net / revenue
        ni = [r * (margin + d.gauss(0, 0.02)) for r in rev[:3]] + [prior["net_income"], net]
        cash_ratio = ocf / net if net else 1.0
        ocf_hist = [v * cash_ratio * drift(0, 0.08) for v in ni[:3]] + [
            prior["operating_cash_flow"], ocf]
        capex_hist = [r * capex / revenue * drift(0, 0.1) for r in rev[:3]] + [
            prior["capex"], capex]
        sh = [prior["shares_diluted"], shares_diluted]
        for _ in range(3):
            sh.insert(0, sh[0] / (1 + dilution + d.gauss(0, 0.01)))
        history = {
            "revenue": rev, "net_income": ni, "operating_cash_flow": ocf_hist,
            "capex": capex_hist, "shares_diluted": sh,
        }

        return Fundamentals(
            company=f"{symbol} Holdings (demo)",
            sic=REPRESENTATIVE_SIC[sector_of(symbol)],
            fiscal_year=_ANCHOR.year - 1,
            revenue_prior=revenue_prior,
            shares_outstanding=shares,
            source="demo",
            prior=prior,
            history={k: list(zip(ends, v, strict=True)) for k, v in history.items()},
            **current,
        )
