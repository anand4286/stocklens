"""Persistence: SQLite by default, PostgreSQL in production, same code.

Two tables, both append-only:

* `analysis_snapshots` — every composite the service computes, written before
  anyone knows the outcome. Resolved later against what the price actually
  did, it is the unbiased sample the evaluation harness scores. A model that
  only remembers the calls it was asked about cannot be graded.
* `agent_runs` — one row per analyst-agent run: model, every tool call with
  its arguments, token usage, latency, the validated report and its grounding
  score. The audit trail an AI feature needs before anyone should trust it.

SQLAlchemy Core rather than the ORM: the queries are few and explicit, and
nothing here benefits from identity maps or lazy loading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine
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
