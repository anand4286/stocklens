"""Build the StockLens API console — a single static page for GitHub Pages.

    python scripts/build_docs_site.py            # writes site/index.html

Nothing in the page is hand-maintained. The endpoint list, parameters and
summaries come from the running app's OpenAPI document; every example response
is captured by calling the endpoint on deterministic demo data; the agent tools
come from `TOOL_SPECS`; the architecture diagrams are lifted from the app's own
/architecture page. Re-run it after changing the API and the console cannot
drift from the code.

It never reaches the network: demo data, an in-memory database, the rules
analyst. So the published page contains no keys, no account data and nothing
about a real market — only the shape of the API and synthetic examples.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from stocklens import __version__  # noqa: E402
from stocklens.agent.tools import TOOL_SPECS  # noqa: E402
from stocklens.app import create_app  # noqa: E402
from stocklens.config import Settings  # noqa: E402

OUT = ROOT / "site" / "index.html"
SYMBOL = "MSFT"

#: Curated context per operation. OpenAPI says *what* an endpoint is; this says
#: what question it answers and what makes it different, which is what a reader
#: of a console actually needs.
GROUPS = [
    ("stock", "📈", "Stock analytics", "One symbol, every engine: score, quant, drivers, research."),
    ("record", "🎯", "Track record", "Committed forecasts, graded in public. The part data sites skip."),
    ("portfolio", "🧮", "Portfolio lab", "Covariance, random-matrix noise and five weighting methods."),
    ("agent", "🤖", "AI analyst", "A Claude agent over read-only tools, every cited figure verified."),
    ("ops", "⚙️", "Platform & ops", "Metadata, probes and the snapshot ledger."),
]
META: dict[tuple[str, str], dict] = {
    ("GET", "/api/v1/symbols/{symbol}/report"): {
        "group": "stock", "title": "Full stock report", "status": "live",
        "what": "Everything the stock page renders, computed in one pass (~250 ms): quote, the "
                "regime-conditioned composite with per-family contributions, technicals, risk, "
                "quant (regime, GARCH, PSR), drivers (factor attribution, shocks, filtered "
                "historical simulation), structure (volume profile, analogs), research (Piotroski, "
                "Altman, Beneish, DCF grid, reverse DCF), news and the committed forecast.",
        "why": "One object is shared by the page, the agent and the evaluator, so they can never "
               "disagree about a number. Viewing it commits that day's forecast to the ledger.",
        "fields": [
            ("composite.score", "Weighted mean of available directional families, −1…+1"),
            ("composite.contributions[]", "Per family: score, weight, contribution (sums to score), rule"),
            ("composite.regime_weights", "Family weights after the regime tilt (total unchanged)"),
            ("forecast.p_up", "Committed P(close higher in 21 sessions) — graded on the track record"),
            ("quant.regime", "trending / mean_reverting / random, from Hurst + variance ratio"),
            ("quant.volatility.garch", "GARCH(1,1) α, β, persistence, half-life, long-run vol"),
            ("drivers.attribution", "Betas with Newey–West t, alpha, variance shares, tail dependence"),
            ("drivers.simulation", "GARCH-filtered historical simulation: percentiles, ES vs normal"),
            ("research.piotroski / altman / beneish", "Forensic scores with every input shown"),
            ("research.valuation.dcf.grid", "Per-share value across discount × terminal growth"),
        ],
    },
    ("GET", "/api/v1/symbols/{symbol}/evidence"): {
        "group": "stock", "title": "Evidence lab", "status": "live",
        "what": "Every price family re-scored at each past session from data up to that day only, "
                "then tested: rank IC at 5 and 21 sessions with t-stats on non-overlapping days, "
                "permutation and circular-shift p-values, score bands, by year and regime, "
                "long/flat and long/short rules after 10 bps costs, PSR and deflated Sharpe.",
        "why": "Answers 'does this indicator actually work on this stock?'. A family 'works' only "
               "with t ≥ 2, p below a Bonferroni α and DSR > 0.95 — usually it does not, and the "
               "response says so.",
        "fields": [
            ("families[].horizons.{5,21}", "ic, ic_independent, t_stat, permutation_p, shift_p"),
            ("families[].rules.long_flat", "annual_return, sharpe, max_drawdown, turnover, psr, dsr"),
            ("families[].verdict / works", "Plain-language verdict and the boolean behind it"),
            ("tests_run / bonferroni_alpha", "How many tests were charged for, and the bar each must clear"),
            ("equity_curves", "Rebased after-cost curves, ≤ 260 points"),
        ],
    },
    ("GET", "/api/v1/symbols/{symbol}/forecasts"): {
        "group": "record", "title": "Symbol forecast ledger", "status": "live",
        "what": "This symbol's live ledger (append-only, write-once resolution) and its simulated "
                "walk-forward record, each scored with Brier skill, reliability and a block t-test.",
        "why": "Every forecast is written before its outcome exists, so the live record cannot be "
               "cherry-picked. The simulated record is labelled as such and never pooled with it.",
        "fields": [
            ("live.forecasts[]", "made_on, p_up, direction, range, model_version, outcome once resolved"),
            ("*.scoring.brier_skill", "1 − Brier / Brier(quoting the base rate); > 0 is information"),
            ("*.scoring.reliability[]", "Predicted vs observed frequency per bin, Wilson 95%"),
            ("*.scoring.block_test", "t-stat of Brier improvement over independent 21-session blocks"),
            ("*.scoring.verdict", "insufficient · skill · no_skill · worse_than_climatology"),
        ],
    },
    ("GET", "/api/v1/track-record"): {
        "group": "record", "title": "Track record", "status": "live",
        "what": "Every forecast this deployment has committed, scored, beside the simulated record "
                "over a fixed, published basket.",
        "why": "'Skill' needs positive Brier skill, a hit-rate interval above 50% on the effective "
               "sample, and t ≥ 2 across independent blocks. On demo noise it correctly says no.",
        "fields": [
            ("universe", "The fixed basket the simulated record walks — chosen before the result"),
            ("live.scoring / simulated.scoring", "Same grading for both, never mixed"),
            ("simulated.method", "The point-in-time discipline, stated"),
        ],
    },
    ("GET", "/api/v1/portfolio"): {
        "group": "portfolio", "title": "Portfolio lab", "status": "live",
        "what": "For 2–25 symbols: sample correlation, Ledoit–Wolf shrinkage intensity, the "
                "eigenvalue spectrum against the Marchenko–Pastur noise band, and equal / "
                "inverse-vol / minimum-variance / risk-parity / HRP weights with effective number "
                "of bets, risk contributions and diversification ratio.",
        "why": "Five correlated names are close to one bet. This measures how many independent "
               "bets a basket really is, from a covariance matrix that is worth using.",
        "fields": [
            ("spectrum.signal_factors", "Eigenvalues above the noise edge (1 + √(N/T))²"),
            ("shrinkage.intensity", "Ledoit–Wolf weight on the constant-correlation target"),
            ("methods.{key}.weights", "Long-only, sum to 1"),
            ("methods.{key}.effective_bets", "Meucci entropy over uncorrelated directions"),
            ("hrp.order", "Quasi-diagonal order from single-linkage clustering"),
        ],
        "query": {"symbols": "AAPL,MSFT,NVDA,JPM,XOM,KO"},
    },
    ("POST", "/api/v1/symbols/{symbol}/analyst"): {
        "group": "agent", "title": "AI analyst note", "status": "live",
        "what": "Runs (or returns the cached) analyst note: Claude drives ten read-only tools over "
                "this symbol's report, then submits a schema-validated note. Without a model key a "
                "deterministic rules brief meets the same contract.",
        "why": "Every cited figure is checked against the value a tool actually returned; the "
               "verification and the full tool trace come back with the note.",
        "fields": [
            ("report", "stance, headline, summary, bull/bear points, risks, cited metrics"),
            ("grounding", "verified / total and a per-citation check"),
            ("tool_calls", "The full trace: tool, step, ok"),
            ("mode / model / tokens / latency_ms", "What ran and what it cost"),
        ],
    },
    ("GET", "/api/v1/meta"): {
        "group": "ops", "title": "Service metadata", "status": "live",
        "what": "Version, data sources, published weights and the agent mode.",
        "why": "Lets a client label demo data and know whether the analyst is a model or rules.",
    },
    ("GET", "/api/v1/symbols/{symbol}/history"): {
        "group": "ops", "title": "Snapshot ledger", "status": "live",
        "what": "The first composite recorded per symbol per session.",
        "why": "An unbiased sample for evaluation: written before anyone knows the outcome.",
    },
    ("GET", "/healthz"): {"group": "ops", "title": "Liveness", "status": "live",
                          "what": "The process is up.", "why": "Kubernetes / load balancer probe."},
    ("GET", "/readyz"): {"group": "ops", "title": "Readiness", "status": "live",
                         "what": "The database is reachable.", "why": "Gate traffic until ready."},
}

REPORT_SECTIONS = [
    ("quote", "Price, change, 52-week range, trailing returns"),
    ("composite", "Score, confidence, coverage, stance, rule, contributions, base vs regime weights"),
    ("forecast", "The committed call: p_up, direction, 1σ range, features, model_version"),
    ("technicals", "RSI, SMAs, MACD histogram, Bollinger, ATR, stochastic"),
    ("risk", "Volatility, EWMA, drawdown, VaR/ES, Sharpe, Sortino, beta, Hurst, VR, range cone"),
    ("quant", "regime · volatility (GARCH, forecast, range estimators, cone, distribution) · sharpe"),
    ("drivers", "sector · attribution · shocks · simulation"),
    ("structure", "volume_profile (POC, value area, level test) · analogs"),
    ("research", "piotroski · altman · beneish · accruals · red_flags · valuation · debate · checklist"),
    ("fundamentals", "Annual XBRL figures and the eight balance-sheet checks"),
    ("news", "Headlines with lexicon tone, links restricted to http(s)"),
    ("chart", "Aligned series for the price, RSI and MACD charts"),
]


def trim(value, depth: int = 0):
    """Keep examples readable: long lists shortened, deep trees left intact."""
    if isinstance(value, list):
        if len(value) > 6:
            return [trim(v, depth + 1) for v in value[:3]] + [f"… {len(value) - 3} more items"]
        return [trim(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: trim(v, depth + 1) for k, v in value.items()}
    return value


def capture() -> tuple[dict, list[dict]]:
    settings = Settings(database_url="sqlite:///:memory:", log_json=False, log_level="WARNING",
                        _env_file=None)
    with TestClient(create_app(settings)) as client:
        spec = client.get("/openapi.json").json()
        ops = []
        for path, methods in spec["paths"].items():
            for method, op in methods.items():
                key = (method.upper(), path)
                meta = META.get(key, {"group": "ops", "title": op.get("summary", path)})
                url = path.replace("{symbol}", SYMBOL)
                query = meta.get("query", {})
                response = client.request(method.upper(), url, params=query)
                example = response.json() if response.headers.get(
                    "content-type", "").startswith("application/json") else None
                params = [{"name": p["name"], "in": p["in"], "required": p.get("required", False),
                           "type": p.get("schema", {}).get("type", "string"),
                           "description": p.get("description", "")}
                          for p in op.get("parameters", [])]
                qs = "?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""
                ops.append({
                    "method": method.upper(), "path": path, "group": meta["group"],
                    "title": meta["title"], "summary": op.get("summary", ""),
                    "doc": (op.get("description") or "").strip(),
                    "what": meta.get("what", ""), "why": meta.get("why", ""),
                    "fields": meta.get("fields", []), "status": meta.get("status", "live"),
                    "params": params, "example_url": url + qs,
                    "status_code": response.status_code, "example": trim(example),
                    "bytes": len(response.content),
                })
    return spec, ops


def diagrams() -> str:
    """The proof loop and the system diagram, lifted from the app's own page."""
    page = (ROOT / "src/stocklens/web/architecture.html").read_text()
    svgs = re.findall(r'<svg class="diagram".*?</svg>', page, flags=re.S)
    return "\n".join(f'<div class="diagram-card">{s}</div>' for s in svgs[:2])


def main() -> int:
    spec, ops = capture()
    tools = [{"name": t["name"], "description": t["description"],
              "input": list(t["input_schema"].get("properties", {}))} for t in TOOL_SPECS]
    collected = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, check=False).stdout
    tests = sum(1 for line in collected.splitlines() if "::" in line)
    data = {
        "version": __version__, "openapi": spec.get("openapi"), "symbol": SYMBOL,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "groups": [{"key": k, "icon": i, "name": n, "blurb": b} for k, i, n, b in GROUPS],
        "ops": ops, "tools": tools, "sections": REPORT_SECTIONS, "tests": tests,
    }
    template = (ROOT / "scripts" / "docs_site_template.html").read_text()
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    page = (template.replace("{{DATA}}", payload)
            .replace("{{DIAGRAMS}}", diagrams())
            .replace("{{VERSION}}", html.escape(__version__)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    (OUT.parent / ".nojekyll").write_text("")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(ops)} operations, {len(tools)} tools, "
          f"{len(page) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
