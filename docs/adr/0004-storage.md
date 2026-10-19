# ADR 0004 — SQLAlchemy Core; SQLite by default, PostgreSQL in production

**Status:** accepted

## Context
Two append-only tables, a handful of queries, and deploy targets ranging from a
laptop to Kubernetes.

## Decision
SQLAlchemy Core (not the ORM) with portable types. SQLite needs no setup and
suits a single instance; PostgreSQL is selected by URL for anything with more
than one replica.

## Consequences
- Zero-config local runs and tests (in-memory SQLite with a static pool).
- Identical code in production; JSON columns map to `JSON`/`JSONB`-compatible types.
- Schema is created at start-up; non-additive changes will need Alembic.
- The daily agent budget and result cache become global as soon as replicas share PostgreSQL.
