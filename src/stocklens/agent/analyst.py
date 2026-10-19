"""The analyst agent: a bounded tool-use loop with an audit trail.

Why a hand-written loop and not the SDK's tool runner: every step here is a
control point — a step ceiling, per-call audit records, a terminal
`submit_report` tool whose input is re-validated and grounded, and explicit
handling of every stop reason. Owning the loop keeps those visible in forty
lines rather than spread over runner hooks.

Without an Anthropic key the same contract is met by `rules_report`, a
deterministic brief written from the same numbers. The page labels which one
it is showing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from .prompts import SYSTEM_PROMPT, user_prompt
from .tools import TOOL_SPECS, AnalystReport, Toolbox, ValidationError, grounding, validate_report

log = logging.getLogger(__name__)

#: Server-side refusal fallback: if the primary model declines, the API
#: re-runs the request on a fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class RunResult:
    mode: str
    model: str | None
    status: str
    report: AnalystReport | None
    grounding: dict | None
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_record(self, symbol: str) -> dict:
        return {
            "symbol": symbol, "mode": self.mode, "model": self.model, "status": self.status,
            "steps": self.steps, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "latency_ms": self.latency_ms,
            "tool_calls": self.tool_calls,
            "report": self.report.model_dump() if self.report else None,
            "grounding": self.grounding, "error": self.error,
        }


def run_model_agent(report: dict, client, model: str, effort: str, max_steps: int) -> RunResult:
    """Drive Claude through the tools until it submits a report or a limit stops it."""
    import anthropic  # deferred: the rules path must not need the SDK configured

    toolbox = Toolbox(report)
    messages: list = [{"role": "user", "content": user_prompt(report["symbol"])}]
    result = RunResult("model", model, "incomplete", None, None)
    started = time.monotonic()

    try:
        for step in range(1, max_steps + 1):
            result.steps = step
            response = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOL_SPECS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                # Each step resends the whole transcript; caching the prefix
                # makes steps 2..n bill the history at the cache-read rate.
                cache_control={"type": "ephemeral"},
            )
            result.input_tokens += response.usage.input_tokens or 0
            result.output_tokens += response.usage.output_tokens or 0
            result.model = response.model

            if response.stop_reason == "refusal":
                result.status = "refused"
                result.error = "the model declined this request"
                break
            if response.stop_reason == "max_tokens":
                result.error = "response hit max_tokens before finishing"
                break
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            uses = [b for b in response.content if b.type == "tool_use"]
            if not uses:
                result.error = "model ended without calling submit_report"
                break

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for use in uses:
                args = use.input if isinstance(use.input, dict) else {}
                record = {"step": step, "tool": use.name, "args": args}
                if use.name == "submit_report":
                    try:
                        final = validate_report(args)
                    except ValidationError as exc:
                        record["ok"] = False
                        result.tool_calls.append(record)
                        results.append({"type": "tool_result", "tool_use_id": use.id,
                                        "is_error": True,
                                        "content": "Report failed validation: "
                                                   + exc.errors()[0]["msg"]})
                        continue
                    record["ok"] = True
                    result.tool_calls.append(record)
                    result.report = final
                    result.grounding = grounding(final, toolbox.seen)
                    result.status = "ok"
                    results.append({"type": "tool_result", "tool_use_id": use.id,
                                    "content": "Report received."})
                else:
                    content = toolbox.call(use.name, args)
                    record["ok"] = True
                    record["bytes"] = len(content)
                    result.tool_calls.append(record)
                    results.append({"type": "tool_result", "tool_use_id": use.id,
                                    "content": content})
            # All results for one assistant turn go back in a single message.
            messages.append({"role": "user", "content": results})
            if result.status == "ok":
                break
        else:
            result.error = f"stopped at the {max_steps}-step ceiling"
    except anthropic.RateLimitError:
        result.status, result.error = "error", "model rate limit reached; try later"
    except anthropic.AuthenticationError:
        result.status, result.error = "error", "model credentials were rejected"
    except anthropic.APIStatusError as exc:
        result.status, result.error = "error", f"model API returned HTTP {exc.status_code}"
    except anthropic.APIConnectionError:
        result.status, result.error = "error", "could not reach the model API"
    finally:
        result.latency_ms = int((time.monotonic() - started) * 1000)

    log.info("agent run finished", extra={
        "symbol": report["symbol"], "status": result.status, "steps": result.steps,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "latency_ms": result.latency_ms})
    return result


def rules_report(report: dict) -> RunResult:
    """A deterministic brief from the same numbers. No model, no cost, no variance."""
    started = time.monotonic()
    toolbox = Toolbox(report)
    for name in ("get_overview", "get_signal_breakdown", "get_risk", "get_technicals",
                 "get_quant", "get_market_structure", "get_research", "get_drivers"):
        toolbox.call(name, {})
    comp, q, risk = report["composite"], report["quote"], report["risk"]
    directional = [c for c in comp["contributions"] if c["kind"] == "directional"]
    available = [c for c in directional if c["score"] is not None]
    bulls = sorted((c for c in available if c["score"] > 0.15), key=lambda c: -c["score"])
    bears = sorted((c for c in available if c["score"] < -0.15), key=lambda c: c["score"])
    stance = {"Constructive": "constructive", "Cautious": "cautious",
              "Neutral": "neutral"}.get(comp["stance"], "insufficient_evidence")

    def line(c):
        return f"{c['name'].replace('_', ' ').title()}: {c['reason']}"

    vol = risk.get("volatility_ewma") or risk.get("volatility_annual")
    cites = [("composite.score", comp["score"]), ("composite.confidence", comp["confidence"]),
             ("quote.price", q["price"])]
    if vol is not None:
        cites.append(("risk.volatility_ewma" if risk.get("volatility_ewma") else
                      "risk.volatility_annual", vol))
    if risk.get("max_drawdown") is not None:
        cites.append(("risk.max_drawdown", risk["max_drawdown"]))

    quant = report.get("quant") or {}
    regime = quant.get("regime") or {}
    garch = (quant.get("volatility") or {}).get("garch")
    research = report.get("research") or {}
    if garch:
        cites.append(("quant.garch.persistence", garch["persistence"]))
    attribution = (report.get("drivers") or {}).get("attribution")
    if attribution:
        cites.append(("drivers.attribution.market_beta", attribution["market_beta"]["value"]))
    piotroski = (research.get("piotroski") or {}).get("score")
    if piotroski is not None:
        cites.append(("research.piotroski.score", piotroski))

    summary = (
        f"{report['symbol']} scores {comp['score']:+.2f} on a -1 to +1 scale with "
        f"{comp['confidence']:.0%} confidence, from {len(available)} of "
        f"{len(directional)} directional evidence families. "
        + (f"The regime reads {regime['trend'].replace('_', '-')}, so family weights are "
           f"{'unchanged' if regime['trend'] == 'random' else 'tilted to match'}. "
           if regime else "")
        + (f"Annualised volatility is {vol:.0%}"
           + (f", with GARCH persistence {garch['persistence']:.2f}. " if garch else ". ")
           if vol else "")
        + (f"The strongest support is {bulls[0]['name'].replace('_', ' ')}. " if bulls else "")
        + (f"The main drag is {bears[0]['name'].replace('_', ' ')}." if bears else "")
    )[:1500]
    debate = research.get("debate") or {"bull": [], "bear": []}
    key_risks = [
        f"One-day 95% VaR of {risk['var_95_1d']:.1%}" if risk.get("var_95_1d") else
        "Too little history for a historical VaR",
        f"Worst drawdown in the last year {risk['max_drawdown']:.1%}"
        if risk.get("max_drawdown") is not None else "Drawdown unavailable",
    ]
    altman = research.get("altman") or {}
    if altman.get("zone") == "distress":
        key_risks.append(altman["reading"])
    if (research.get("beneish") or {}).get("flagged"):
        key_risks.append(research["beneish"]["reading"])
    final = AnalystReport(
        stance=stance,
        headline=f"{report['symbol']}: {comp['stance'].lower()} on the blended evidence",
        summary=summary,
        bull_points=([line(c) for c in bulls[:3]] + debate["bull"][:2])[:5],
        bear_points=([line(c) for c in bears[:3]] + debate["bear"][:2])[:5],
        key_risks=key_risks[:5],
        cited_metrics=[{"metric": m, "value": v} for m, v in cites],
        confidence=comp["confidence"],
    )
    return RunResult("rules", None, "ok", final, grounding(final, toolbox.seen),
                     latency_ms=int((time.monotonic() - started) * 1000),
                     tool_calls=[{"tool": "rules_engine", "ok": True}])


def to_response(result: RunResult | dict, cached: bool = False) -> dict:
    data = result.as_record("") if isinstance(result, RunResult) else dict(result)
    data.pop("symbol", None)
    created = data.get("created_at")
    if created is not None and not isinstance(created, str):
        data["created_at"] = created.isoformat(timespec="seconds")
    data["cached"] = cached
    # Tool-call arguments for submit_report duplicate `report`; keep the trace lean.
    data["tool_calls"] = [
        {k: v for k, v in c.items() if k != "args" or c.get("tool") != "submit_report"}
        for c in data.get("tool_calls") or []
    ]
    return json.loads(json.dumps(data, default=str))
