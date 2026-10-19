"""Shared fixtures. Tests never reach the network: demo data, in-memory DB, fake model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from stocklens.app import create_app
from stocklens.config import Settings


@pytest.fixture(autouse=True)
def _no_env_credentials(monkeypatch):
    """A developer's real keys in the environment must not change test behaviour."""
    for name in ("STOCKLENS_API_KEY", "STOCKLENS_ANTHROPIC_API_KEY", "STOCKLENS_ALPACA_API_KEY_ID",
                 "STOCKLENS_ALPACA_API_SECRET_KEY", "STOCKLENS_SEC_USER_AGENT",
                 "STOCKLENS_DATA_PROVIDER", "STOCKLENS_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides) -> Settings:
    base = {"database_url": "sqlite:///:memory:", "log_json": False, "log_level": "WARNING",
            "_env_file": None}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def client():
    with TestClient(create_app(make_settings())) as c:
        yield c


def tool_use(name: str, args: dict, id_: str = "tu_1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def response(*blocks, stop="tool_use", tokens=(100, 50)):
    return SimpleNamespace(stop_reason=stop, content=list(blocks), model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


class FakeAnthropic:
    """Replays scripted responses; records every request it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        step = self.script.pop(0)
        return step(kwargs) if callable(step) else step
