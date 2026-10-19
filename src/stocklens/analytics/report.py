"""Assemble everything known about one symbol into one report.

This is the only module that touches both the data facade and the analytics,
and it is what the API, the agent's tools and the evaluation harness share —
so the page, the agent and the backtest can never disagree about a number.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import UTC, datetime

from ..data import Bar, DataUnavailable, MarketData, sectors
from ..forecast.model import make_forecast
from ..quant import factors, inference, regime, scenarios, similarity, volatility, volume_profile
from ..research import report as research_report
from . import indicators as ind
from . import risk, signals
from .composite import DEFAULT_WEIGHTS, blend

#: Three years: GARCH needs 250 returns to fit, and the analog search needs
#: room for ten non-overlapping 30-session windows plus their outcomes.
HISTORY_DAYS = 756


def _r(x, n=2):
    return None if x is None or not math.isfinite(x) else round(x, n)


def _safe_url(url: str | None) -> str | None:
    """Only http(s) links reach the page; a `javascript:` URL from a feed would not."""
    return url if url and url.startswith(("https://", "http://")) else None


def chart_series(bars: list[Bar], last: int = 260) -> dict:
    closes = [b.close for b in bars]
    sma20, sma50 = ind.sma_series(closes, 20), ind.sma_series(closes, 50)
    rsi = ind.rsi_series(closes)
    macd, sig, hist = ind.macd_series(closes)
    upper, lower = [], []
    for i in range(len(closes)):
        band = ind.bollinger(closes[: i + 1]) if i >= 19 else None
        lower.append(band[0] if band else None)
        upper.append(band[2] if band else None)
    s = slice(-last, None)
    return {
        "dates": [b.day.isoformat() for b in bars][s],
        "open": [b.open for b in bars][s],
        "high": [b.high for b in bars][s],
        "low": [b.low for b in bars][s],
        "close": closes[s],
        "volume": [b.volume for b in bars][s],
        "sma20": [_r(v) for v in sma20][s],
        "sma50": [_r(v) for v in sma50][s],
        "bb_upper": [_r(v) for v in upper][s],
        "bb_lower": [_r(v) for v in lower][s],
        "rsi14": [_r(v, 1) for v in rsi][s],
        "macd": [_r(v, 3) for v in macd][s],
        "macd_signal": [_r(v, 3) for v in sig][s],
        "macd_hist": [_r(v, 3) for v in hist][s],
    }


def quote(bars: list[Bar]) -> dict:
    last, prev = bars[-1], bars[-2] if len(bars) > 1 else bars[-1]
    closes = [b.close for b in bars]
    year = bars[-252:]
    return {
        "as_of": last.day.isoformat(),
        "price": last.close,
        "change": _r(last.close - prev.close),
        "change_pct": _r(last.close / prev.close - 1, 4) if prev.close else None,
        "open": last.open, "high": last.high, "low": last.low, "volume": last.volume,
        "high_52w": max(b.high for b in year),
        "low_52w": min(b.low for b in year),
        "return_1m": _r(ind.pct_change(closes, 21), 4),
        "return_3m": _r(ind.pct_change(closes, 63), 4),
        "return_1y": _r(ind.pct_change(closes, 252), 4),
    }


def _aligned(bars: list[Bar], other: list[Bar]) -> tuple[list[float], list[float], list[str]]:
    """Closes on the dates both series traded, oldest first."""
    by_day = {b.day: b.close for b in other}
    kept = [b for b in bars if b.day in by_day]
    return [b.close for b in kept], [by_day[b.day] for b in kept], [b.day.isoformat() for b in kept]


def drivers(bars: list[Bar], bench: list[Bar], sector_bars: list[Bar], sector: str | None,
            sector_etf: str | None) -> dict:
    """What moves this stock, and what a market shock would do to it."""
    closes = [b.close for b in bars]
    out: dict = {"sector": sector, "sector_label": sectors.SECTOR_LABELS.get(sector or ""),
                 "sector_etf": sector_etf, "attribution": None, "shocks": [],
                 "simulation": scenarios.simulate(closes)}
    if not bench or bench is bars:
        return out
    stock, market, dates = _aligned(bars, bench)
    sector_closes = None
    if sector_bars:
        by_day = {b.day.isoformat(): b.close for b in sector_bars}
        keep = [i for i, d in enumerate(dates) if d in by_day]
        stock, market = [stock[i] for i in keep], [market[i] for i in keep]
        dates = [dates[i] for i in keep]
        sector_closes = [by_day[d] for d in dates]
    out["attribution"] = factors.attribution(stock, market, sector_closes, dates)
    out["shocks"] = scenarios.shocks(stock, market, sector_closes)
    return out


def build(symbol: str, market: MarketData, benchmark_symbol: str = "SPY") -> dict:
    bars = market.bars(symbol, HISTORY_DAYS)
    if len(bars) < 2:
        raise DataUnavailable(f"not enough history for {symbol}")
    try:
        bench = market.bars(benchmark_symbol, HISTORY_DAYS) if symbol != benchmark_symbol else bars
    except DataUnavailable:
        bench = []
    news = market.news(symbol, 30)
    fundamentals = market.fundamentals(symbol)
    sector = sectors.sector_for_symbol(symbol, fundamentals.sic if fundamentals else None)
    sector_etf = sectors.etf_for(sector)
    sector_bars: list[Bar] = []
    # A fund regressed on itself, or on the market it is, is a degenerate fit.
    if sector_etf not in (symbol, benchmark_symbol) and symbol not in sectors.ETF_SECTORS:
        try:
            sector_bars = market.bars(sector_etf, HISTORY_DAYS)
        except DataUnavailable:
            sector_bars = []
    price = bars[-1].close

    fundamental_signal, checks = signals.fundamental(fundamentals)
    family = [
        signals.technical(bars),
        signals.momentum(bars),
        signals.relative_strength(bars, bench) if bench else signals._abstain(
            "relative_strength", "directional", "benchmark unavailable"),
        fundamental_signal,
        signals.valuation(fundamentals, price),
        signals.sentiment(news),
        signals.volume(bars),
        signals.news_flow(news),
    ]
    closes = [b.close for b in bars]
    # The regime is read first because it decides how much to trust each
    # family: an oversold RSI is an invitation in a mean-reverting market and
    # a warning in a trending one. A "random" regime leaves the weights alone.
    market_regime = regime.classify(closes)
    regime_weights = regime.tilt_weights(DEFAULT_WEIGHTS, market_regime)
    composite = blend(family, regime_weights)
    technicals = {
        "rsi14": _r(ind.rsi_series(closes)[-1], 1),
        "sma20": _r(ind.sma(closes, 20)),
        "sma50": _r(ind.sma(closes, 50)),
        "sma200": _r(ind.sma(closes, 200)),
        "atr14": _r(ind.atr([b.high for b in bars], [b.low for b in bars], closes)),
        "stochastic_k": _r(ind.stochastic_k([b.high for b in bars], [b.low for b in bars],
                                            closes), 1),
        "bollinger": [_r(x) for x in ind.bollinger(closes)] if len(closes) >= 20 else None,
        "macd_histogram": _r(ind.macd_series(closes)[2][-1], 3),
    }

    return {
        "symbol": symbol,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": market.sources,
        "benchmark": benchmark_symbol,
        "quote": quote(bars),
        "composite": {**composite.as_dict(), "regime": market_regime["trend"],
                      "base_weights": DEFAULT_WEIGHTS, "regime_weights": regime_weights},
        "weights": regime_weights,
        "technicals": technicals,
        "risk": risk.summary(closes, [b.close for b in bench] if bench else None),
        "quant": {
            "regime": market_regime,
            "volatility": volatility.summary({
                "open": [b.open for b in bars], "high": [b.high for b in bars],
                "low": [b.low for b in bars], "close": closes}),
            "sharpe": inference.sharpe_inference(risk.log_returns(closes[-253:])),
        },
        "drivers": drivers(bars, bench, sector_bars, sector, sector_etf if sector_bars else None),
        "structure": {
            "volume_profile": volume_profile.build(bars),
            "analogs": similarity.analogs(bars),
        },
        # The call this report commits to. The route writes it to the ledger;
        # it is built from the same bars, so the page and the record agree.
        "forecast": make_forecast(bars, bench or None),
        "research": research_report.build(fundamentals, price),
        "fundamentals": None if fundamentals is None else {
            **{k: v for k, v in asdict(fundamentals).items()
               if k not in ("extra", "prior", "history")},
            "checks": [asdict(c) for c in checks],
        },
        "news": [
            {"published": n.published.isoformat(timespec="minutes"), "headline": n.headline,
             "source": n.source, "url": _safe_url(n.url), "tone": signals.headline_tone(n.headline)}
            for n in news[:20]
        ],
        "chart": chart_series(bars),
        "disclaimer": "Educational analytics, not investment advice.",
    }
