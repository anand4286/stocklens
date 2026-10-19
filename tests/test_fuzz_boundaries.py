"""Property-based fuzzing of every boundary where untrusted input enters.

Example-based tests check the inputs someone thought of. These generate
thousands of inputs nobody thought of — arbitrary Unicode, control
characters, path separators, encoded newlines, oversized strings — and assert
the invariants that the security model rests on hold for all of them.
"""

from __future__ import annotations

import json
import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from stocklens.data import TTLCache
from stocklens.data.base import SYMBOL_PATTERN, normalise_symbol
from stocklens.logging_setup import JsonFormatter, log_safe
from tests.conftest import make_settings

FUZZ = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
hostile_text = st.text(alphabet=st.characters(), max_size=300)


@FUZZ
@given(hostile_text)
def test_a_ticker_either_matches_the_allowlist_or_is_rejected(raw):
    """Nothing but [A-Z.-] can ever reach an upstream URL."""
    try:
        symbol = normalise_symbol(raw)
    except ValueError:
        return
    assert SYMBOL_PATTERN.fullmatch(symbol)
    assert re.fullmatch(r"[A-Z.\-]{1,8}", symbol)
    assert not {"/", "?", "#", "%", "\\", ":", "\n", "\r", " "} & set(symbol)


@FUZZ
@given(hostile_text, st.integers(min_value=1, max_value=512))
def test_log_fields_never_carry_control_characters(value, limit):
    out = log_safe(value, limit)
    assert len(out) <= limit
    assert "\n" not in out and "\r" not in out
    assert all(ch.isprintable() for ch in out)


@FUZZ
@given(st.dictionaries(st.sampled_from(["symbol", "path", "reason"]), hostile_text, max_size=3))
def test_every_json_log_line_is_one_parseable_line(fields):
    import logging

    record = logging.LogRecord("stocklens.test", logging.INFO, __file__, 1, "msg", None, None)
    for key, value in fields.items():
        setattr(record, key, value)
    line = JsonFormatter().format(record)
    assert "\n" not in line
    assert json.loads(line)["msg"] == "msg"


@FUZZ
@given(hostile_text)
def test_cors_accepts_only_exact_http_origins(origin):
    try:
        allowed = make_settings(cors_origins=[origin]).cors_origins
    except ValidationError:
        return
    (value,) = allowed
    assert value != "*"
    assert value.startswith(("https://", "http://"))
    assert value.count("/") == 2  # scheme://host[:port], no path


@FUZZ
@given(st.lists(st.tuples(st.integers(0, 50), st.integers()), max_size=200),
       st.integers(min_value=1, max_value=20))
def test_cache_never_exceeds_its_bound_and_returns_what_was_stored(ops, bound):
    cache = TTLCache(3600, max_items=bound)
    for key, value in ops:
        assert cache.get_or_set((key,), lambda value=value: value) is not None
        assert len(cache._items) <= bound


@pytest.fixture(scope="module")
def api():
    from fastapi.testclient import TestClient

    from stocklens.app import create_app

    with TestClient(create_app(make_settings(rate_limit_per_minute=10**7,
                                             compute_rate_limit_per_minute=10**7))) as c:
        yield c


@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=40))
def test_the_api_never_errors_on_a_hostile_ticker(api, raw):
    """Any path segment yields a controlled 4xx or a valid report — never a 500."""
    from urllib.parse import quote

    response = api.get(f"/api/v1/symbols/{quote(raw, safe='')}/report")
    assert response.status_code in (200, 404, 405, 422), response.status_code
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
