# API reference

Base path `/api/v1`. JSON only. Interactive docs at `/docs`; the machine
schema at `/openapi.json`.

## Authentication

None by default (public read-only showcase). If `STOCKLENS_API_KEY` is set,
every `/api` route needs:

```
Authorization: Bearer <key>
```

Model-backed analyst runs additionally need the key unless
`STOCKLENS_AGENT_PUBLIC=true`.

## Conventions

- **Tickers** are case-insensitive and must match `^[A-Z]{1,5}([.-][A-Z]{1,2})?$` after upper-casing (`AAPL`, `brk.b`, `BF-B`).
- **Absent values** are `null`, never `0`.
- **Ratios** are fractions (`0.053` = 5.3%). Money is USD.
- Every response has `X-Request-ID`; quote it when reporting a problem.
- API responses are `Cache-Control: no-store`.

## Errors

| Status | Meaning | Body |
|---|---|---|
| 401 | Missing or wrong API key | `{"detail": "missing or invalid API key"}` |
| 404 | No data for the symbol | `{"detail": "..."}` |
| 422 | Invalid ticker | `{"detail": "invalid ticker symbol"}` |
| 429 | Rate limited | `{"detail": "rate limit exceeded"}` + `Retry-After` |
| 500 | Unexpected error | `{"detail": "internal error", "request_id": "..."}` |

## `GET /api/v1/meta`

```json
{
  "version": "1.0.0",
  "sources": {"prices": "demo", "news": "demo", "fundamentals": "demo"},
  "benchmark": "SPY",
  "weights": {"technical": 0.2, "momentum": 0.2, "relative_strength": 0.15,
              "fundamental": 0.2, "valuation": 0.1, "sentiment": 0.15},
  "agent": {"mode": "rules", "model": null, "public": false},
  "demo": true
}
```

## `GET /api/v1/symbols/{symbol}/report`

Everything the stock page renders. Abbreviated; values are illustrative:

```json
{
  "symbol": "MSFT",
  "generated_at": "2026-10-19T14:02:11+00:00",
  "sources": {"prices": "alpaca", "news": "alpaca", "fundamentals": "sec-edgar"},
  "quote": {"as_of": "2026-10-16", "price": 512.4, "change_pct": 0.0081,
            "high_52w": 555.1, "low_52w": 401.2, "return_1y": 0.214},
  "composite": {
    "score": 0.284, "confidence": 0.91, "coverage": 0.85, "conviction": 0.22,
    "stance": "Constructive",
    "rule": {"constructive_at": 0.2, "cautious_at": -0.2, "min_confidence": 0.45},
    "contributions": [
      {"name": "momentum", "kind": "directional", "score": 0.61, "weight": 0.2,
       "effective_weight": 0.2353, "contribution": 0.1435,
       "reason": "12-1 month return +24.4%; above rising 50/200-day averages",
       "detail": {"momentum": 0.2441, "sma50": 498.2, "sma200": 470.9}}
    ]
  },
  "technicals": {"rsi14": 58.2, "sma20": 505.1, "sma50": 498.2, "sma200": 470.9,
                 "atr14": 8.4, "stochastic_k": 71.3, "bollinger": [488.0, 505.1, 522.2],
                 "macd_histogram": 1.12},
  "risk": {"volatility_annual": 0.241, "volatility_ewma": 0.218, "max_drawdown": -0.187,
           "var_95_1d": 0.0231, "es_95_1d": 0.0342, "sharpe": 0.94, "sortino": 1.31,
           "beta": 1.08, "hurst": 0.531, "variance_ratio_5d": 0.97,
           "range_cone": [{"sessions": 5, "sigma_pct": 0.0307, "low": 496.9, "high": 528.4}],
           "observations": 399},
  "fundamentals": {"company": "MICROSOFT CORPORATION", "fiscal_year": 2026,
                   "revenue": 331839000000.0, "checks": [{"name": "profitable", "passed": true,
                   "value": 133749000000.0, "rule": "net income above zero"}]},
  "news": [{"published": "2026-10-16T13:05+00:00", "headline": "...", "source": "...",
            "url": "https://...", "tone": 0.5}],
  "chart": {"dates": ["..."], "open": [], "high": [], "low": [], "close": [], "volume": [],
            "sma20": [], "sma50": [], "bb_upper": [], "bb_lower": [],
            "rsi14": [], "macd": [], "macd_signal": [], "macd_hist": []},
  "quant": {
    "regime": {"trend": "random", "volatility": "normal", "hurst": 0.48, "variance_ratio": 0.97,
               "agree": true, "reason": "...", "multipliers": {"technical": 1.0}},
    "volatility": {"garch": {"alpha": 0.08, "beta": 0.89, "persistence": 0.97,
                             "half_life_sessions": 22.8, "long_run_vol_annual": 0.25},
                   "forecast": [{"sessions": 21, "vol_annual": 0.23}],
                   "range_estimators": {"close_to_close": 0.22, "parkinson": 0.2},
                   "cone": [{"window": 21, "p10": 0.14, "p50": 0.2, "p90": 0.31, "current": 0.22}],
                   "distribution": {"skewness": -0.4, "excess_kurtosis": 3.1}},
    "sharpe": {"sharpe_annual": 0.94, "psr_vs_zero": 0.82, "min_track_record_sessions": 690}
  },
  "structure": {
    "volume_profile": {"poc": 498.1, "value_area_low": 470.2, "value_area_high": 521.9,
                       "position": "inside_value", "bins": [], "level_test": {"lift": 1.05}},
    "analogs": {"matches": [], "edge": -0.004, "significant": false, "verdict": "..."}
  },
  "research": {
    "piotroski": {"score": 7, "max_known": 9, "tests": []},
    "altman": {"z": 3.1, "variant": "Z''", "zone": "safe"},
    "beneish": {"m": -2.4, "model": "8-variable", "flagged": false},
    "red_flags": [{"name": "Dilution", "measured": 0.031, "threshold": 0.02, "severity": 0.4,
                   "raised": true}],
    "valuation": {"multiples": {"pe": 34.1}, "dcf": {"grid": [[]]},
                  "reverse_dcf": {"implied_growth": 0.12, "historical_growth": 0.14}},
    "debate": {"bull": [], "bear": []}, "checklist": []
  },
  "disclaimer": "Educational analytics, not investment advice."
}
```

`composite` also carries `regime`, `base_weights` and `regime_weights`; the
contributions use the regime weights. Every section is `null` (or has `null`
fields) below its sample floor. Field definitions: [QUANT.md](QUANT.md).

Side effect: records the first composite per symbol per session in
`analysis_snapshots`.

## `GET /api/v1/symbols/{symbol}/history`

```json
{"symbol": "MSFT", "snapshots": [
  {"id": 12, "symbol": "MSFT", "as_of": "2026-10-16", "price": 512.4, "score": 0.284,
   "confidence": 0.91, "stance": "Constructive",
   "families": {"technical": 0.12, "momentum": 0.61}, "provider": "alpaca",
   "created_at": "2026-10-16T21:04:55"}]}
```

## `POST /api/v1/symbols/{symbol}/analyst`

No body. Returns a model-written note when allowed, otherwise the rules brief.

```json
{
  "mode": "model",
  "model": "claude-opus-5-5",
  "status": "ok",
  "steps": 3,
  "input_tokens": 9120,
  "output_tokens": 1430,
  "latency_ms": 21450,
  "cached": false,
  "note": null,
  "tool_calls": [
    {"step": 1, "tool": "get_overview", "args": {}, "ok": true, "bytes": 812},
    {"step": 1, "tool": "get_signal_breakdown", "args": {}, "ok": true, "bytes": 1904},
    {"step": 2, "tool": "get_risk", "args": {}, "ok": true, "bytes": 640},
    {"step": 3, "tool": "submit_report", "ok": true}
  ],
  "report": {
    "stance": "constructive",
    "headline": "MSFT: trend and quality carry a modestly constructive read",
    "summary": "...",
    "bull_points": ["..."], "bear_points": ["..."], "key_risks": ["..."],
    "cited_metrics": [{"metric": "composite.score", "value": 0.284}],
    "confidence": 0.7
  },
  "grounding": {"verified": 4, "total": 4, "score": 1.0, "checks": [
    {"metric": "composite.score", "cited": 0.284, "actual": 0.284, "verified": true, "why": null}]}
}
```

`note` explains any downgrade ("No model is configured…", "The daily model
budget is spent…", "The model run did not complete…"). `cached: true` means a
stored run from within `STOCKLENS_AGENT_CACHE_MINUTES` was returned without a
model call.

## `GET /api/v1/symbols/{symbol}/evidence`

Every price family tested walk-forward on this stock: rank IC by horizon with
t-stats, permutation and circular-shift p-values, score bands, by year, by
regime, long/flat and long/short economics after costs with PSR and deflated
Sharpe, equity curves, and a verdict per family. ~0.5 s uncached; cached an
hour. 404 below 300 sessions. Shape: [QUANT.md § Evidence lab](QUANT.md).

## `GET /api/v1/symbols/{symbol}/forecasts`

```json
{"symbol": "NVDA",
 "live": {"kind": "live", "forecasts": [{"made_on": "2026-10-19", "p_up": 0.54, "direction": "up",
          "range_low": 3203.0, "range_high": 3984.6, "model_version": "price-v1",
          "resolved_at": null, "outcome_up": null}], "scoring": {"verdict": {"label": "insufficient"}}},
 "simulated": {"kind": "simulated", "scoring": {"brier_skill": -0.056, "verdict": {"label": "no_skill"}},
               "forecasts": []}}
```

## `GET /api/v1/track-record`

The live ledger across every symbol, scored, and the simulated record over the
fixed basket in `universe`. `scoring` carries `brier`, `brier_skill`,
`hit_rate` (with effective Wilson/Hoeffding intervals), `reliability`,
`range_coverage`, `block_test`, `slices` (regime, confidence, family) and
`verdict` (`insufficient | skill | no_skill | worse_than_climatology`).

The report itself also carries `forecast` (the committed call) and `drivers`
(`sector`, `sector_etf`, `attribution`, `shocks`, `simulation`). Viewing a
report writes its forecast to the ledger once per session.

## `GET /api/v1/portfolio?symbols=AAPL,MSFT,...`

Correlation structure of a basket and five ways to weight it. 2–25 symbols,
each validated against the ticker pattern before any upstream call (422
otherwise); a symbol without data is listed under `skipped` rather than
failing the basket. 404 if fewer than two symbols have 60 aligned returns.

```json
{
  "symbols": ["AAPL", "MSFT", "JPM"], "observations": 299,
  "correlation": [[1.0, 0.41, 0.22], [0.41, 1.0, 0.3], [0.22, 0.3, 1.0]],
  "shrinkage": {"intensity": 0.25, "target": "constant_correlation", "average_correlation": 0.31},
  "spectrum": {"eigenvalues": [1.7, 0.8, 0.5], "mp_lower": 0.81, "mp_upper": 1.21, "q": 0.01,
               "signal_factors": 1, "first_factor_share": 0.57},
  "methods": {
    "hrp": {"label": "Hierarchical risk parity", "rule": "...",
            "weights": {"AAPL": 0.31, "MSFT": 0.28, "JPM": 0.41}, "volatility_annual": 0.19,
            "effective_bets": 1.9, "risk_contributions": {"AAPL": 0.33}, "max_weight": 0.41,
            "diversification_ratio": 1.4}
  },
  "hrp": {"order": ["AAPL", "MSFT", "JPM"], "linkage": [[0, 1, 0.54, 2]]},
  "reading": "3 names, 1.9 effective bets held equally: one common factor explains 57% of variance.",
  "skipped": {}
}
```

Method keys: `equal`, `inverse_vol`, `min_variance`, `risk_parity`, `hrp`.

## Probes

| Path | 200 when | Otherwise |
|---|---|---|
| `/healthz` | the process serves requests | — |
| `/readyz` | the database answers | 503 `{"status": "unavailable"}` |
