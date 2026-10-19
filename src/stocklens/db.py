"""Persistence: SQLite by default, PostgreSQL in production, same code.

Three tables, all append-only:

* `analysis_snapshots` — every composite the service computes, written before
  anyone knows the outcome. Resolved later against what the price actually
  did, it is the unbiased sample the evaluation harness scores. A model that
  only remembers the calls it was asked about cannot be graded.
* `agent_runs` — one row per analyst-agent run: model, every tool call with
  its arguments, token usage, latency, the validated report and its grounding
  score. The audit trail an AI feature needs before anyone should trust it.
* `forecasts` — the falsifiable ledger (`stocklens.forecast`): one committed
  probabilistic call per symbol per session per model version, written before
  the outcome exists. The outcome columns start empty and are filled exactly
  once; nothing else in a row is ever updated.

SQLAlchemy Core rather than the ORM: the queries are few and explicit, and
nothing here benefits from identity maps or lazy loading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

metadata = MetaData()

analysis_snapshots = Table(
    "analysis_snapshots", metadata,
    Column("id", Integer, primary_key=True),
    Column("symbol", String(12), nullable=False, index=True),
    Column("as_of", String(10), nullable=False),
    Column("price", Float, nullable=False),
    Column("score", Float, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("stance", String(32), nullable=False),
    Column("families", JSON, nullable=False),
    Column("provider", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)

agent_runs = Table(
    "agent_runs", metadata,
    Column("id", Integer, primary_key=True),
    Column("symbol", String(12), nullable=False, index=True),
    Column("mode", String(16), nullable=False),  # "model" or "rules"
    Column("model", String(64)),
    Column("status", String(16), nullable=False),  # ok | error | refused | incomplete
    Column("steps", Integer, nullable=False, default=0),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("latency_ms", Integer, nullable=False, default=0),
    Column("tool_calls", JSON, nullable=False),
    Column("report", JSON),
    Column("grounding", JSON),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)

forecasts = Table(
    "forecasts", metadata,
    Column("id", Integer, primary_key=True),
    Column("symbol", String(12), nullable=False, index=True),
    Column("made_on", String(10), nullable=False),  # ISO date of the decision session
    Column("horizon", Integer, nullable=False),  # sessions
    Column("model_version", String(32), nullable=False),
    Column("p_up", Float, nullable=False),
    Column("direction", String(4), nullable=False),  # up | down
    Column("score", Float, nullable=False),
    Column("price", Float, nullable=False),
    Column("range_low", Float),
    Column("range_high", Float),
    Column("features", JSON, nullable=False),
    Column("regime", String(32)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
    Column("outcome_price", Float),
    Column("outcome_up", Boolean),
    Column("in_range", Boolean),
    # One forecast per symbol per session per model: refreshing a page cannot
    # stack the record with copies of the same call.
    UniqueConstraint("symbol", "made_on", "model_version", name="uq_forecast_symbol_day_model"),
)


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///"):
            path = Path(url.removeprefix("sqlite:///"))
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
        if url == "sqlite:///:memory:":
            # One shared connection, or every checkout would see an empty database.
            self.engine: Engine = create_engine(
                url, poolclass=StaticPool, connect_args={"check_same_thread": False})
        else:
            self.engine = create_engine(url, pool_pre_ping=True)

    def create_all(self) -> None:
        metadata.create_all(self.engine)

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(select(1))
        return True

    # -- snapshots ---------------------------------------------------------

    def record_snapshot(self, report: dict, provider: str) -> bool:
        """Record the first composite per symbol per session; later views are no-ops.

        One row per session keeps the ledger a clean panel (symbol x day) for
        evaluation, however many times a page is refreshed.
        """
        comp = report["composite"]
        with self.engine.begin() as conn:
            exists = conn.execute(select(analysis_snapshots.c.id).where(
                analysis_snapshots.c.symbol == report["symbol"],
                analysis_snapshots.c.as_of == report["quote"]["as_of"],
                analysis_snapshots.c.provider == provider,
            ).limit(1)).first()
            if exists:
                return False
            conn.execute(insert(analysis_snapshots).values(
                symbol=report["symbol"], as_of=report["quote"]["as_of"],
                price=report["quote"]["price"], score=comp["score"],
                confidence=comp["confidence"], stance=comp["stance"],
                families={c["name"]: c["score"] for c in comp["contributions"]},
                provider=provider, created_at=datetime.now(UTC),
            ))
        return True

    def snapshots(self, symbol: str, limit: int = 30) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(analysis_snapshots).where(analysis_snapshots.c.symbol == symbol)
                .order_by(analysis_snapshots.c.created_at.desc()).limit(limit)
            ).mappings().all()
        return [dict(r) for r in rows]

    # -- agent runs --------------------------------------------------------

    def record_agent_run(self, **values) -> int:
        values.setdefault("created_at", datetime.now(UTC))
        with self.engine.begin() as conn:
            result = conn.execute(insert(agent_runs).values(**values))
            return int(result.inserted_primary_key[0])

    def recent_agent_run(self, symbol: str, within_minutes: int) -> dict | None:
        if within_minutes <= 0:
            return None
        since = datetime.now(UTC) - timedelta(minutes=within_minutes)
        with self.engine.connect() as conn:
            row = conn.execute(
                select(agent_runs).where(
                    agent_runs.c.symbol == symbol,
                    agent_runs.c.status == "ok",
                    agent_runs.c.created_at >= since,
                ).order_by(agent_runs.c.created_at.desc()).limit(1)
            ).mappings().first()
        return dict(row) if row else None

    def model_runs_today(self) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.engine.connect() as conn:
            return int(conn.execute(
                select(func.count()).select_from(agent_runs).where(
                    agent_runs.c.mode == "model", agent_runs.c.created_at >= start)
            ).scalar_one())

    # -- forecasts -----------------------------------------------------------

    def forecasts_written_today(self) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.engine.connect() as conn:
            return int(conn.execute(
                select(func.count()).select_from(forecasts).where(
                    forecasts.c.created_at >= start)).scalar_one())

    def record_forecast(self, f: dict) -> bool:
        """Insert a forecast unless one exists for (symbol, made_on, model_version).

        Returns whether a row was written. The first call of the session is
        the one that counts; a later call with different inputs is not allowed
        to replace it, or the ledger would be revisable after the fact.
        """
        key = (forecasts.c.symbol == f["symbol"], forecasts.c.made_on == f["made_on"],
               forecasts.c.model_version == f["model_version"])
        try:
            with self.engine.begin() as conn:
                if conn.execute(select(forecasts.c.id).where(*key).limit(1)).first():
                    return False
                conn.execute(insert(forecasts).values(
                    symbol=f["symbol"], made_on=f["made_on"], horizon=f["horizon"],
                    model_version=f["model_version"], p_up=f["p_up"],
                    direction=f["direction"], score=f["score"], price=f["price"],
                    range_low=f.get("range_low"), range_high=f.get("range_high"),
                    features=f.get("features") or {}, regime=f.get("regime"),
                    created_at=datetime.now(UTC),
                ))
        except IntegrityError:
            return False  # a concurrent request won the race; its row stands
        return True

    def due_forecasts(self, symbol: str) -> list[dict]:
        """Unresolved forecasts for `symbol`, oldest first. Whether each is due
        depends on the bars, which `forecast.ledger.resolve_due` checks."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(forecasts).where(forecasts.c.symbol == symbol,
                                        forecasts.c.resolved_at.is_(None))
                .order_by(forecasts.c.made_on)
            ).mappings().all()
        return [dict(r) for r in rows]

    def resolve_forecast(self, forecast_id: int, outcome_price: float, outcome_up: bool,
                         in_range: bool | None) -> bool:
        """Fill in the outcome once. Returns False if the row was already resolved."""
        with self.engine.begin() as conn:
            result = conn.execute(
                update(forecasts)
                .where(forecasts.c.id == forecast_id, forecasts.c.resolved_at.is_(None))
                .values(resolved_at=datetime.now(UTC), outcome_price=outcome_price,
                        outcome_up=bool(outcome_up),
                        in_range=None if in_range is None else bool(in_range))
            )
            return result.rowcount == 1

    def forecasts(self, symbol: str | None = None, resolved: bool | None = None,
                  limit: int = 500) -> list[dict]:
        """Newest decision first. `resolved` filters on whether the outcome is in."""
        query = select(forecasts)
        if symbol is not None:
            query = query.where(forecasts.c.symbol == symbol)
        if resolved is True:
            query = query.where(forecasts.c.resolved_at.is_not(None))
        elif resolved is False:
            query = query.where(forecasts.c.resolved_at.is_(None))
        query = query.order_by(forecasts.c.made_on.desc(), forecasts.c.id.desc()).limit(limit)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [dict(r) for r in rows]
