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
  "disclaimer": "Educational analytics, not investment advice."
}
```

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

## Probes

| Path | 200 when | Otherwise |
|---|---|---|
| `/healthz` | the process serves requests | — |
| `/readyz` | the database answers | 503 `{"status": "unavailable"}` |
