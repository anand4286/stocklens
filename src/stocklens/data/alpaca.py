"""Alpaca market data: daily bars and news, read-only.

Uses only the market-data host. This module has no order, account or position
endpoint in it — StockLens is analytics, and a codebase that cannot place a
trade cannot be tricked into placing one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr

from .base import Bar, DataUnavailable, NewsItem

DATA_HOST = "https://data.alpaca.markets"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class _AlpacaClient:
    def __init__(self, key_id: SecretStr, secret: SecretStr) -> None:
        self._headers = {
            "APCA-API-KEY-ID": key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": secret.get_secret_value(),
            "Accept": "application/json",
        }

    def get(self, path: str, params: dict) -> dict:
        try:
            response = httpx.get(f"{DATA_HOST}{path}", params=params,
                                 headers=self._headers, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise DataUnavailable(f"alpaca unreachable: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            # Never echo the body: some upstreams reflect the presented key.
            raise DataUnavailable("alpaca rejected the credentials")
        if response.status_code >= 400:
            raise DataUnavailable(f"alpaca returned HTTP {response.status_code}")
        return response.json()


class AlpacaPrices:
    name = "alpaca"

    def __init__(self, key_id: SecretStr, secret: SecretStr, feed: str = "iex") -> None:
        self._client = _AlpacaClient(key_id, secret)
        self._feed = feed

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        # Calendar days back, padded for weekends and holidays.
        start = datetime.now(UTC) - timedelta(days=int(days * 1.5) + 10)
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "limit": 10000,
            "adjustment": "all",
            "feed": self._feed,
        }
        bars: list[Bar] = []
        page_token = None
        for _ in range(10):  # bounded pagination
            if page_token:
                params["page_token"] = page_token
            payload = self._client.get(f"/v2/stocks/{symbol}/bars", params)
            for row in payload.get("bars") or []:
                bars.append(Bar(
                    day=datetime.fromisoformat(row["t"].replace("Z", "+00:00")).date(),
                    open=float(row["o"]), high=float(row["h"]), low=float(row["l"]),
                    close=float(row["c"]), volume=float(row["v"]),
                ))
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        if not bars:
            raise DataUnavailable(f"no bars for {symbol}")
        return bars[-days:]


class AlpacaNews:
    name = "alpaca"

    def __init__(self, key_id: SecretStr, secret: SecretStr) -> None:
        self._client = _AlpacaClient(key_id, secret)

    def news(self, symbol: str, limit: int) -> list[NewsItem]:
        start = datetime.now(UTC) - timedelta(days=14)
        payload = self._client.get("/v1beta1/news", {
            "symbols": symbol,
            "limit": max(1, min(limit, 50)),
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sort": "desc",
        })
        items = []
        for row in payload.get("news") or []:
            items.append(NewsItem(
                published=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
                headline=str(row.get("headline") or "")[:300],
                source=str(row.get("source") or "unknown")[:60],
                url=row.get("url"),
                summary=(str(row.get("summary") or "")[:600] or None),
            ))
        return items
