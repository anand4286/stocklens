# ADR 0001 — A pure analytics core

**Status:** accepted

## Context
Scoring logic is the part most likely to be wrong and most expensive to be
wrong about. It is also needed in three places: the web page, the agent's
tools, and the evaluation harness.

## Decision
`analytics/` contains pure functions over plain dataclasses. It imports no
provider, framework, database or network code. One `report.build()` composes
it with the data facade, and every consumer uses that one function.

## Consequences
- Every formula is unit-testable without mocks; the test suite runs in about a second with no network.
- The page, the agent and the backtest cannot disagree about a number.
- Adding a data source never touches the maths.
- Cost: the report is computed eagerly even when a consumer needs only part of it. At this size (milliseconds) that is the right trade.
