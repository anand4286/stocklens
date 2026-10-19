"""The shared TTL cache: expiry, bounds, and single-flight under concurrency."""

from __future__ import annotations

import contextlib
import threading
import time

from stocklens.data import TTLCache


def test_concurrent_misses_compute_once():
    cache, calls = TTLCache(60), []

    def slow():
        calls.append(1)
        time.sleep(0.05)
        return "value"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_set(("k",), slow)))
               for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["value"] * 16
    assert len(calls) == 1


def test_failures_are_not_cached_and_do_not_wedge_the_key():
    cache, attempts = TTLCache(60), []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("upstream down")
        return "ok"

    with contextlib.suppress(RuntimeError):
        cache.get_or_set(("k",), flaky)
    assert cache.get_or_set(("k",), flaky) == "ok"


def test_size_is_bounded_and_entries_expire():
    cache = TTLCache(0, max_items=3)
    for i in range(10):
        cache.get_or_set((i,), lambda i=i: i)
    assert len(cache._items) <= 3
    assert cache.get_or_set((9,), lambda: "recomputed") == "recomputed"  # ttl 0: always stale


def test_report_is_served_from_cache(client):
    first = client.get("/api/v1/symbols/MSFT/report").json()
    second = client.get("/api/v1/symbols/MSFT/report").json()
    assert first["generated_at"] == second["generated_at"]


def test_cache_size_follows_the_memory_setting():
    from fastapi.testclient import TestClient

    from stocklens.app import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings(cache_max_symbols=16))
    with TestClient(app) as c:
        for i in range(30):
            c.get(f"/api/v1/symbols/Q{chr(65 + i // 26)}{chr(65 + i % 26)}/report")
        assert len(app.state.reports._items) <= 16
        assert len(app.state.market._cache._items) <= 3 * 16 + 64
