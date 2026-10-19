"""Runtime configuration, read once from the environment.

Every secret is a `SecretStr`, so it never appears in a repr, a log line or a
traceback. Nothing here has a real credential as a default: an unconfigured
deployment runs in offline demo mode rather than failing open.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STOCKLENS_", env_file=".env", extra="ignore")

    # -- data ---------------------------------------------------------------
    #: `demo` is deterministic synthetic data: no network, no keys, safe for CI
    #: and for a public showcase. `alpaca` reads real market data with the
    #: operator's own (free) Alpaca market-data keys.
    data_provider: Literal["demo", "alpaca"] = "demo"
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    #: `iex` is the free feed. `sip` needs a paid plan.
    alpaca_feed: Literal["iex", "sip"] = "iex"
    #: SEC fair-access policy requires a descriptive User-Agent with a contact.
    #: Unset means live fundamentals are off.
    sec_user_agent: str | None = None
    #: Benchmark used for beta and relative strength.
    benchmark_symbol: str = "SPY"
    cache_ttl_seconds: int = Field(default=300, ge=0)

    # -- storage ------------------------------------------------------------
    database_url: str = "sqlite:///./var/stocklens.db"

    # -- security -----------------------------------------------------------
    #: When set, every /api route needs `Authorization: Bearer <key>`.
    #: Unset makes the read-only analytics public (the showcase mode); the
    #: agent endpoint stays protected separately by `agent_public`.
    api_key: SecretStr | None = None
    #: Requests per minute per client IP, for the whole API.
    rate_limit_per_minute: int = Field(default=60, ge=1)
    #: Origins allowed to call the API from a browser. Empty = same-origin only.
    cors_origins: list[str] = []

    @field_validator("cors_origins")
    @classmethod
    def _exact_origins_only(cls, origins: list[str]) -> list[str]:
        """A wildcard would let any site read the API with a visitor's browser."""
        for origin in origins:
            if origin == "*" or not origin.startswith(("https://", "http://")) or (
                    origin.rstrip("/").count("/") > 2):
                raise ValueError(f"CORS origin must be exact scheme://host[:port]: {origin!r}")
        return [o.rstrip("/") for o in origins]

    #: Trust X-Forwarded-For only behind a proxy you control.
    trust_proxy_headers: bool = False

    # -- the analyst agent --------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    agent_model: str = "claude-opus-5-5"
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    #: Hard ceiling on model round-trips per run. The agent has six read-only
    #: tools; a complete analysis needs 2-4 turns, so 8 leaves room without
    #: letting a confused run loop.
    agent_max_steps: int = Field(default=8, ge=1, le=20)
    #: A fresh run for the same symbol within this window returns the stored
    #: one. Caps spend and makes the endpoint idempotent under refresh-spam.
    agent_cache_minutes: int = Field(default=60, ge=0)
    #: Global daily ceiling on model-backed runs, counted from the database so
    #: it survives restarts and holds across replicas sharing one database.
    agent_daily_limit: int = Field(default=50, ge=0)
    #: Allow anonymous callers to trigger model-backed runs. Off by default:
    #: a public endpoint that spends money is an invitation.
    agent_public: bool = False
    agent_rate_limit_per_minute: int = Field(default=3, ge=1)

    # -- compute and storage ceilings ---------------------------------------
    #: Per-client limit on the endpoints that re-score history or fetch many
    #: symbols (evidence, portfolio, forecasts, track record). Each costs
    #: 0.5-2 s of CPU uncached, so the general API limit is far too generous.
    compute_rate_limit_per_minute: int = Field(default=10, ge=1)
    #: Ceiling on new rows written to the snapshot and forecast ledgers per UTC
    #: day. A ledger written on GET is a storage-growth vector: without a cap,
    #: cycling through valid tickers grows the database without bound.
    ledger_daily_limit: int = Field(default=5000, ge=0)

    # -- logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
