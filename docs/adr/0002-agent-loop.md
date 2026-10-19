# ADR 0002 — Hand-written agent loop with a terminal submit tool

**Status:** accepted

## Context
The analyst agent must produce output that can be verified, at bounded cost,
with an audit trail. Options considered: a single prompt with the report pasted
in; the SDK's tool runner; a hand-written loop.

## Decision
A hand-written loop (~60 lines) over `messages.create`, with:
- tools that are read-only views over a precomputed report, bound to one symbol;
- a terminal `submit_report` tool with a strict schema as the only way to finish;
- Pydantic re-validation and a grounding check of every cited metric;
- explicit handling of every stop reason and a step ceiling;
- one audit row per run.

## Why not the alternatives
- **Single prompt:** cheapest, but the model never declares which figures it used, so nothing can be verified.
- **Tool runner:** a good default for most agents; here every step is a control point (ceiling, audit, validation, grounding) and keeping them in one visible function makes review easier than spreading them across hooks.

## Consequences
- Grounding becomes a measurable property stored per run.
- A deterministic rules engine can satisfy the same contract, which gives a no-key mode and a baseline.
- We own loop maintenance as the API evolves.
