"""The evidence families. Each scores a symbol on [-1, +1] or abstains.

Contract
--------
* +1 argues the outlook is constructive, -1 that it is not, 0 is neutral.
* A family with no usable data returns `score=None`, which is **not** 0.
  Unknown and neutral are different claims; averaging them together would
  quietly dilute strong evidence with missing evidence.
* Every score carries a `reason` a reader can disagree with, and the inputs
  that produced it in `detail`. A number without its rule is an opinion.

Two kinds of family
-------------------
*Directional* families (technical, momentum, fundamental, valuation,
sentiment, relative strength) push the composite up or down. *Conviction*
families (volume, news flow) say how much is going on, not which way; they
scale confidence instead. A crowded stock is not thereby a good one.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from ..data.base import Bar, Fundamentals, NewsItem
from . import indicators as ind

Kind = Literal["directional", "conviction"]


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    if x is None or not math.isfinite(x):
        return 0.0
    return max(lo, min(hi, x))


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    kind: Kind
    score: float | None
    reason: str
    detail: dict[str, float | str | None] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.score is not None

    def as_dict(self) -> dict:
        data = asdict(self)
        data["score"] = None if self.score is None else round(self.score, 3)
        return data


def _abstain(name: str, kind: Kind, reason: str) -> Signal:
    return Signal(name, kind, None, reason)


# -- price-based ---------------------------------------------------------------


def technical(bars: list[Bar]) -> Signal:
    """Short-horizon price action: RSI mean reversion, tempered by trend and MACD.

    RSI carries 60% because mean reversion over days is the premise; trend and
    MACD carry 20% each so the family does not argue for a falling knife just
    because it is oversold.
    """
    closes = [b.close for b in bars]
    if len(closes) < 35:
        return _abstain("technical", "directional", "needs 35 sessions of history")
    rsi = ind.rsi_series(closes)[-1]
    sma20 = ind.sma(closes, 20)
    _, _, hist = ind.macd_series(closes)
    price = closes[-1]

    rsi_score = 1.0 if rsi <= 30 else -1.0 if rsi >= 70 else (50 - rsi) / 20
    # 10% above the 20-day average saturates the trend leg.
    trend_score = clamp((price - sma20) / sma20 * 10)
    # A histogram of 2% of price saturates the MACD leg.
    macd_score = clamp((hist[-1] or 0) / (price * 0.02))
    score = clamp(0.6 * rsi_score + 0.2 * trend_score + 0.2 * macd_score)
    return Signal("technical", "directional", score,
                  f"RSI {rsi:.0f}; price {(price / sma20 - 1):+.1%} vs 20-day average",
                  {"rsi14": round(rsi, 2), "sma20": round(sma20, 2),
                   "macd_histogram": round(hist[-1] or 0, 4)})


def momentum(bars: list[Bar]) -> Signal:
    """12-1 month momentum (Jegadeesh & Titman 1993), with a 50/200-day check.

    The most recent month is skipped: one-month returns tend to reverse, and
    including them blends a reversal effect into a momentum measurement.
    """
    closes = [b.close for b in bars]
    if len(closes) < 252:
        if len(closes) < 130:
            return _abstain("momentum", "directional", "needs 6 months of history")
        lookback = len(closes) - 22
    else:
        lookback = 231  # 252 - 21
    past, recent = closes[-lookback - 22], closes[-22]
    mom = recent / past - 1
    sma50, sma200 = ind.sma(closes, 50), ind.sma(closes, 200)
    # +/-40% over the window saturates; that is roughly the 10th/90th
    # percentile of 12-1 momentum across US large caps.
    mom_score = clamp(mom / 0.40)
    align = 0.0
    if sma50 and sma200:
        align = 1.0 if closes[-1] > sma50 > sma200 else -1.0 if closes[-1] < sma50 < sma200 else 0
    score = clamp(0.75 * mom_score + 0.25 * align)
    months = round((lookback + 21) / 21)
    return Signal("momentum", "directional", score,
                  f"{months}-1 month return {mom:+.1%}"
                  + ("; above rising 50/200-day averages" if align > 0 else
                     "; below falling 50/200-day averages" if align < 0 else ""),
                  {"momentum": round(mom, 4), "sma50": sma50 and round(sma50, 2),
                   "sma200": sma200 and round(sma200, 2)})


def relative_strength(bars: list[Bar], benchmark: list[Bar], window: int = 63) -> Signal:
    """Excess return over the benchmark across one quarter.

    20 percentage points saturates: across large caps the absolute quarterly
    excess return has a 90th percentile in the mid-teens, so only genuine
    outliers pin at +/-1.
    """
    if len(bars) <= window or len(benchmark) <= window:
        return _abstain("relative_strength", "directional", "no benchmark history")
    mine = bars[-1].close / bars[-window - 1].close - 1
    theirs = benchmark[-1].close / benchmark[-window - 1].close - 1
    excess = mine - theirs
    return Signal("relative_strength", "directional", clamp(excess / 0.20),
                  f"{excess:+.1%} vs benchmark over {window} sessions",
                  {"return": round(mine, 4), "benchmark_return": round(theirs, 4)})


def volume(bars: list[Bar]) -> Signal:
    """Participation: the last session's volume against its 20-day average."""
    if len(bars) < 21:
        return _abstain("volume", "conviction", "needs 21 sessions")
    avg = sum(b.volume for b in bars[-21:-1]) / 20
    if avg <= 0:
        return _abstain("volume", "conviction", "no volume reported")
    ratio = bars[-1].volume / avg
    # 2x saturates high, half saturates low; linear between.
    score = 1.0 if ratio >= 2 else -1.0 if ratio <= 0.5 else clamp((ratio - 1) * 1.5)
    return Signal("volume", "conviction", score, f"volume {ratio:.2f}x the 20-day average",
                  {"last": bars[-1].volume, "average_20d": round(avg)})


# -- filings -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool | None
    value: float | None
    rule: str


def scorecard(f: Fundamentals | None) -> list[Check]:
    """Falsifiable balance-sheet and earnings checks. `passed=None` is unknown."""
    if f is None:
        return []

    def ratio(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    def check(name, value, ok, rule):
        return Check(name, None if value is None else bool(ok(value)), value, rule)

    fcf = (f.operating_cash_flow - f.capex
           if f.operating_cash_flow is not None and f.capex is not None else None)
    return [
        check("profitable", f.net_income, lambda v: v > 0, "net income above zero"),
        check("gross_margin", ratio(f.gross_profit, f.revenue), lambda v: v >= 0.30,
              "gross margin at least 30%"),
        check("operating_margin", ratio(f.operating_income, f.revenue), lambda v: v >= 0.10,
              "operating margin at least 10%"),
        check("revenue_growth",
              None if f.revenue is None or not f.revenue_prior else f.revenue / f.revenue_prior - 1,
              lambda v: v > 0, "revenue grew year on year"),
        check("current_ratio", ratio(f.current_assets, f.current_liabilities),
              lambda v: v >= 1.0, "current assets cover current liabilities"),
        check("debt_to_equity",
              ratio(f.long_term_debt, f.equity) if (f.equity or 0) > 0 else None,
              lambda v: v <= 1.5, "long-term debt at most 1.5x equity"),
        check("free_cash_flow", fcf, lambda v: v > 0, "operating cash flow exceeds capex"),
        check("cash_conversion", ratio(f.operating_cash_flow, f.net_income)
              if (f.net_income or 0) > 0 else None,
              lambda v: v >= 0.8, "operating cash flow at least 80% of net income"),
    ]


def fundamental(f: Fundamentals | None) -> tuple[Signal, list[Check]]:
    checks = scorecard(f)
    known = [c for c in checks if c.passed is not None]
    # Fewer than four answered checks is not a scorecard, it is an anecdote.
    if len(known) < 4:
        return _abstain("fundamental", "directional",
                        "fewer than 4 checks could be answered from filings"), checks
    passed = sum(c.passed for c in known)
    score = (2 * passed - len(known)) / len(known)
    return Signal("fundamental", "directional", score,
                  f"{passed} of {len(known)} filing checks pass",
                  {"passed": passed, "answered": len(known), "fiscal_year": f.fiscal_year}), checks


def valuation(f: Fundamentals | None, price: float) -> Signal:
    """Earnings yield and free-cash-flow yield against fixed bands.

    The bands are picked, and say so: 2% yield is expensive, 8% is cheap, which
    brackets the long-run S&P 500 earnings yield of roughly 4-6%. A sector-
    relative band is the obvious refinement and needs peer data this
    open edition does not ship.
    """
    if f is None or not f.shares_outstanding or price <= 0:
        return _abstain("valuation", "directional", "no share count to value the company")
    cap = price * f.shares_outstanding
    yields = {}
    if f.net_income is not None:
        yields["earnings_yield"] = f.net_income / cap
    if f.operating_cash_flow is not None and f.capex is not None:
        yields["fcf_yield"] = (f.operating_cash_flow - f.capex) / cap
    if not yields:
        return _abstain("valuation", "directional", "no earnings or cash flow reported")
    score = sum(clamp((y - 0.05) / 0.03) for y in yields.values()) / len(yields)
    text = ", ".join(f"{k.replace('_', ' ')} {v:.1%}" for k, v in yields.items())
    return Signal("valuation", "directional", score, text,
                  {"market_cap": round(cap), **{k: round(v, 4) for k, v in yields.items()}})


# -- news ------------------------------------------------------------------------

#: A deliberately small, inspectable finance lexicon (after Loughran & McDonald
#: 2011): general-purpose sentiment lists misread finance, where "liability"
#: and "tax" are not negative. Swap for an LLM classifier if one is configured;
#: this stays as the deterministic fallback.
POSITIVE = {
    "beat", "beats", "raise", "raises", "raised", "upgrade", "upgraded", "record", "growth",
    "strong", "surge", "surges", "wins", "win", "expands", "expanded", "buyback", "outperform",
    "profit", "rally", "approval", "approved", "boost", "tops", "exceeds",
}
NEGATIVE = {
    "miss", "misses", "cut", "cuts", "downgrade", "downgraded", "lawsuit", "probe", "inquiry",
    "slip", "slips", "falls", "plunge", "plunges", "weak", "warning", "recall", "loss",
    "fraud", "investigation", "decline", "declines", "pressure", "concerns", "layoffs", "halt",
}
_WORD = re.compile(r"[a-z]+")


def headline_tone(text: str) -> float | None:
    words = _WORD.findall(text.lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    if pos + neg == 0:
        return None
    return (pos - neg) / (pos + neg)


def sentiment(news: list[NewsItem], now: datetime | None = None) -> Signal:
    """Recency-weighted headline tone; three-day half-life."""
    now = now or datetime.now(UTC)
    weighted = total = 0.0
    scored = 0
    for item in news:
        tone = headline_tone(item.headline)
        if tone is None:
            continue
        age_days = max(0.0, (now - item.published).total_seconds() / 86400)
        w = 0.5 ** (age_days / 3)
        weighted += tone * w
        total += w
        scored += 1
    # Two opinionated headlines are a coin flip, not a reading.
    if scored < 3 or total == 0:
        return _abstain("sentiment", "directional",
                        f"only {scored} headline(s) carried a tone; needs 3")
    score = clamp(weighted / total)
    return Signal("sentiment", "directional", score,
                  f"net tone {score:+.2f} across {scored} toned headlines of {len(news)}",
                  {"scored": scored, "headlines": len(news)})


def news_flow(news: list[NewsItem], now: datetime | None = None) -> Signal:
    """Coverage in the last 3 days against the 14-day daily rate.

    Measured in doublings so a busy name and a quiet one are comparable;
    three doublings above the baseline saturates.
    """
    now = now or datetime.now(UTC)
    if not news:
        return _abstain("news_flow", "conviction", "no coverage found")
    recent = sum(now - n.published <= timedelta(days=3) for n in news)
    window = sum(now - n.published <= timedelta(days=14) for n in news)
    baseline = max(window / 14 * 3, 0.5)
    score = clamp(math.log2((recent + 0.5) / baseline) / 3)
    return Signal("news_flow", "conviction", score,
                  f"{recent} articles in 3 days vs {baseline:.1f} expected",
                  {"recent_3d": recent, "window_14d": window})
