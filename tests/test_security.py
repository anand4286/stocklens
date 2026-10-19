import pytest
from fastapi.testclient import TestClient

from stocklens.app import create_app
from stocklens.security import CSP, RateLimiter

from .conftest import make_settings


def test_every_response_carries_the_security_headers(client):
    for path in ("/", "/s/AAPL", "/architecture", "/healthz", "/api/v1/meta"):
        headers = client.get(path).headers
        assert headers["content-security-policy"] == CSP
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert "max-age" in headers["strict-transport-security"]
        assert headers["x-request-id"]


def test_the_policy_forbids_inline_script_and_foreign_connections():
    assert "script-src 'self';" in CSP
    assert "unsafe-inline" not in CSP
    assert "connect-src 'self'" in CSP
    assert "frame-ancestors 'none'" in CSP


def test_the_ui_contains_no_inline_script_or_style():
    from pathlib import Path

    web = Path(__file__).parents[1] / "src" / "stocklens" / "web"
    for page in web.glob("*.html"):
        text = page.read_text()
        assert "<script>" not in text and "<script type=\"module\">" not in text, page
        assert " style=" not in text and "<style" not in text, page
        assert " onclick=" not in text, page


def test_api_responses_are_not_cached(client):
    assert client.get("/api/v1/meta").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("bad", ["aa$", "AAPL;DROP", "TOOLONGSYM", "A.B.C", "..", "%2e%2e"])
def test_malformed_tickers_never_reach_a_provider(client, bad):
    assert client.get(f"/api/v1/symbols/{bad}/report").status_code in (404, 422)


def test_lowercase_tickers_are_normalised(client):
    assert client.get("/api/v1/symbols/msft/report").json()["symbol"] == "MSFT"


def test_api_key_is_enforced_when_configured():
    app = create_app(make_settings(api_key="s3cret-key-value"))
    with TestClient(app) as c:
        assert c.get("/api/v1/meta").status_code == 401
        assert c.get("/api/v1/meta", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.get("/api/v1/meta", headers={"Authorization": "Bearer s3cret-key-value"})
        assert ok.status_code == 200
        # Probes stay open for the orchestrator.
        assert c.get("/healthz").status_code == 200


def test_rate_limit_returns_429_with_retry_after():
    app = create_app(make_settings(rate_limit_per_minute=3))
    with TestClient(app) as c:
        codes = [c.get("/api/v1/meta").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200]
        limited = c.get("/api/v1/meta")
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
        # Pages are not throttled by the API bucket.
        assert c.get("/").status_code == 200


def test_rate_limiter_refills_and_bounds_memory():
    limiter = RateLimiter(per_minute=60, max_keys=10)
    for i in range(50):
        limiter.allow(f"10.0.0.{i}")
    assert len(limiter._buckets) == 10


def test_proxy_headers_are_ignored_unless_trusted(client):
    # A spoofed X-Forwarded-For must not buy a fresh rate-limit bucket.
    app = create_app(make_settings(rate_limit_per_minute=2))
    with TestClient(app) as c:
        for i in range(2):
            c.get("/api/v1/meta", headers={"X-Forwarded-For": f"1.2.3.{i}"})
        assert c.get("/api/v1/meta", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 429


def test_unhandled_errors_do_not_leak_internals(monkeypatch):
    app = create_app(make_settings())

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("stocklens.analytics.report.build", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/symbols/AAPL/report")
        assert r.status_code == 500
        assert "secret internal detail" not in r.text
        assert r.json()["request_id"]


def test_secrets_never_appear_in_settings_repr():
    s = make_settings(api_key="hunter2-key", anthropic_api_key="sk-ant-xyz")
    assert "hunter2-key" not in repr(s) and "sk-ant-xyz" not in repr(s)


def test_news_links_are_restricted_to_http():
    from stocklens.analytics.report import _safe_url

    assert _safe_url("javascript:alert(1)") is None
    assert _safe_url("data:text/html,x") is None
    assert _safe_url("https://example.com/a") == "https://example.com/a"


def test_compute_heavy_endpoints_have_their_own_rate_limit():
    from fastapi.testclient import TestClient

    from stocklens.app import create_app
    from tests.conftest import make_settings

    with TestClient(create_app(make_settings(compute_rate_limit_per_minute=2))) as c:
        codes = [c.get("/api/v1/portfolio?symbols=AAPL,MSFT").status_code for _ in range(4)]
    assert codes[:2] == [200, 200]
    assert 429 in codes[2:]


def test_ledger_writes_stop_at_the_daily_ceiling():
    from fastapi.testclient import TestClient

    from stocklens.app import create_app
    from tests.conftest import make_settings

    with TestClient(create_app(make_settings(ledger_daily_limit=2))) as c:
        for sym in ("AAPL", "MSFT", "NVDA", "KO"):
            assert c.get(f"/api/v1/symbols/{sym}/report").status_code == 200
        assert c.get("/api/v1/track-record").json()["live"]["count"] == 2


def test_cors_rejects_wildcards_and_paths():
    import pytest
    from pydantic import ValidationError

    from tests.conftest import make_settings

    for bad in (["*"], ["example.com"], ["https://example.com/path"]):
        with pytest.raises(ValidationError):
            make_settings(cors_origins=bad)
    assert make_settings(cors_origins=["https://anand4286.github.io/"]).cors_origins == [
        "https://anand4286.github.io"]


def test_http_clients_do_not_log_upstream_urls():
    import logging

    from stocklens.logging_setup import configure

    configure("INFO", as_json=True)
    for name in ("httpx", "httpcore", "anthropic"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def test_cdn_caching_is_opt_in_and_never_with_an_api_key():
    from fastapi.testclient import TestClient

    from stocklens.app import create_app
    from tests.conftest import make_settings

    with TestClient(create_app(make_settings())) as c:
        assert c.get("/api/v1/symbols/KO/report").headers["cache-control"] == "no-store"
    with TestClient(create_app(make_settings(public_cache_seconds=120))) as c:
        for url in ("/api/v1/symbols/KO/report", "/api/v1/portfolio?symbols=KO,PEP"):
            assert c.get(url).headers["cache-control"].startswith("public, max-age=120")
    with TestClient(create_app(make_settings(public_cache_seconds=120, api_key="k" * 32))) as c:
        r = c.get("/api/v1/symbols/KO/report", headers={"Authorization": "Bearer " + "k" * 32})
        assert r.headers["cache-control"] == "no-store"
