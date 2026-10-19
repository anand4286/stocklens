"""Provider wiring: one `MarketData` facade over three independent sources."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from ..config import Settings
from ..logging_setup import log_safe
from .base import (
    Bar,
    DataUnavailable,
    Fundamentals,
    FundamentalsSource,
    NewsItem,
    NewsSource,
    PriceSource,
    normalise_symbol,
)
from .demo import DemoFundamentals, DemoNews, DemoPrices

log = logging.getLogger(__name__)
T = TypeVar("T")

__all__ = ["Bar", "DataUnavailable", "Fundamentals", "MarketData", "NewsItem",
           "build_market_data", "normalise_symbol"]


class TTLCache:
    """A small thread-safe TTL cache with a size bound.

    Per-process on purpose: it absorbs refresh-spam on one replica. A shared
    cache (Redis) is the documented upgrade when replicas multiply.
    """

    def __init__(self, ttl_seconds: int, max_items: int = 2048) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()
        #: One lock per key being computed right now, so a burst of requests
        #: for a cold key computes it once and the rest wait for that result
        #: (single-flight). Without it, N concurrent misses cost N computations
        #: exactly when the service is busiest.
        self._inflight: dict[tuple, threading.Lock] = {}

    def _fresh(self, key: tuple, now: float):
        hit = self._items.get(key)
        return hit if hit and now - hit[0] < self._ttl else None

    def get_or_set(self, key: tuple, factory: Callable[[], T]) -> T:
        with self._lock:
            hit = self._fresh(key, time.monotonic())
            if hit:
                return hit[1]  # type: ignore[return-value]
            gate = self._inflight.setdefault(key, threading.Lock())
        with gate:
            with self._lock:
                hit = self._fresh(key, time.monotonic())
                if hit:  # another caller computed it while this one waited
                    return hit[1]  # type: ignore[return-value]
            try:
                value = factory()
                with self._lock:
                    if len(self._items) >= self._max and key not in self._items:
                        oldest = min(self._items, key=lambda k: self._items[k][0])
                        del self._items[oldest]
                    self._items[key] = (time.monotonic(), value)
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
        return value


class MarketData:
    def __init__(self, prices: PriceSource, news: NewsSource | None,
                 fundamentals: FundamentalsSource | None, ttl_seconds: int = 300,
                 max_symbols: int = 256) -> None:
        self.prices = prices
        self.news_source = news
        self.fundamentals_source = fundamentals
        # Three entries per symbol (bars, news, filings), plus room for the
        # benchmark and sector funds every report shares.
        self._cache = TTLCache(ttl_seconds, max_items=3 * max_symbols + 64)

    @property
    def sources(self) -> dict[str, str | None]:
        return {
            "prices": self.prices.name,
            "news": self.news_source.name if self.news_source else None,
            "fundamentals": self.fundamentals_source.name if self.fundamentals_source else None,
        }

    def bars(self, symbol: str, days: int = 300) -> list[Bar]:
        return self._cache.get_or_set(("bars", symbol, days),
                                      lambda: self.prices.daily_bars(symbol, days))

    def news(self, symbol: str, limit: int = 20) -> list[NewsItem]:
        if self.news_source is None:
            return []
        try:
            return self._cache.get_or_set(("news", symbol, limit),
                                          lambda: self.news_source.news(symbol, limit))
        except DataUnavailable as exc:
            log.warning("news unavailable",
                        extra={"symbol": log_safe(symbol), "reason": log_safe(exc, 200)})
            return []

    def fundamentals(self, symbol: str) -> Fundamentals | None:
        if self.fundamentals_source is None:
            return None
        try:
            return self._cache.get_or_set(
                ("fundamentals", symbol), lambda: self.fundamentals_source.fundamentals(symbol))
        except DataUnavailable as exc:
            log.warning("fundamentals unavailable",
                        extra={"symbol": log_safe(symbol), "reason": log_safe(exc, 200)})
            return None


def build_market_data(settings: Settings) -> MarketData:
    fundamentals: FundamentalsSource | None = None
    if settings.sec_user_agent:
        from .edgar import EdgarFundamentals

        fundamentals = EdgarFundamentals(settings.sec_user_agent)

    if settings.data_provider == "alpaca":
        if not (settings.alpaca_api_key_id and settings.alpaca_api_secret_key):
            raise RuntimeError("STOCKLENS_DATA_PROVIDER=alpaca needs both Alpaca keys")
        from .alpaca import AlpacaNews, AlpacaPrices

        return MarketData(
            AlpacaPrices(settings.alpaca_api_key_id, settings.alpaca_api_secret_key,
                         settings.alpaca_feed),
            AlpacaNews(settings.alpaca_api_key_id, settings.alpaca_api_secret_key),
            fundamentals,
            settings.cache_ttl_seconds,
            settings.cache_max_symbols,
        )

    return MarketData(DemoPrices(), DemoNews(), fundamentals or DemoFundamentals(),
                      settings.cache_ttl_seconds, settings.cache_max_symbols)
