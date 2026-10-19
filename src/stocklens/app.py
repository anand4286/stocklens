"""The HTTP service: a small read-only API and the static UI that uses it.

    GET  /s/{SYMBOL}                         the stock page
    GET  /architecture                       how it is built, in the app itself
    GET  /api/v1/meta                        version, data sources, agent mode
    GET  /api/v1/symbols/{symbol}/report     everything the page shows
    GET  /api/v1/symbols/{symbol}/history    the recorded composite ledger
    POST /api/v1/symbols/{symbol}/analyst    the AI analyst note
    GET  /portfolio                          the portfolio lab
    GET  /track-record  /why                 the track record; how StockLens differs
    GET  /api/v1/symbols/{symbol}/forecasts  this symbol's ledger and simulated record
    GET  /api/v1/track-record                live ledger + simulated record, scored
    GET  /api/v1/symbols/{symbol}/evidence   every signal family tested on this stock
    GET  /api/v1/portfolio?symbols=A,B,...   correlation spectrum and five weightings
    GET  /healthz  /readyz                   liveness and readiness probes

There is no write endpoint for market state, no account, no order path.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent.analyst import rules_report, run_model_agent, to_response
from .analytics import report as report_builder
from .analytics.composite import DEFAULT_WEIGHTS
from .config import Settings, get_settings
from .data import DataUnavailable, MarketData, TTLCache, build_market_data, normalise_symbol
from .db import Database
from .evidence import test_families
from .forecast import backfill
from .forecast import scoring as forecast_scoring
from .forecast.ledger import resolve_due
from .logging_setup import configure, log_safe
from .quant import portfolio as portfolio_lab
from .security import RateLimiter, SecurityMiddleware, has_valid_key, require_api_key_if_configured

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"

#: The basket the simulated track record is walked over. Fixed, and printed on
#: the page, so nobody can pick the basket after seeing the result.
TRACK_RECORD_UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM",
                         "KO", "PFE", "CAT", "WMT")

#: Swagger UI needs its CDN bundle and an inline bootstrap script, so /docs
#: gets its own, looser policy. Nothing else on the site does.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
    "https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'"
)

#: Length-bounded at the framework edge; the pattern check is `parse_symbol`.
Symbol = Annotated[str, PathParam(min_length=1, max_length=8)]


def create_app(settings: Settings | None = None, market: MarketData | None = None,
               database: Database | None = None, anthropic_client=None) -> FastAPI:
    settings = settings or get_settings()
    configure(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db.create_all()
        log.info("started", extra={"version": __version__, "sources": app.state.market.sources,
                                   "agent": app.state.agent_mode})
        yield

    app = FastAPI(
        title="StockLens API",
        version=__version__,
        summary="Explainable stock analytics with a guarded AI analyst agent.",
        lifespan=lifespan,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.market = market or build_market_data(settings)
    app.state.db = database or Database(settings.database_url)
    app.state.agent_limiter = RateLimiter(settings.agent_rate_limit_per_minute)
    app.state.compute_limiter = RateLimiter(settings.compute_rate_limit_per_minute)
    # Walk-forward records are deterministic for a given day's data and cost
    # around a second; an hour's cache keeps them off the hot path.
    app.state.records = TTLCache(3600, max_items=256)
    # A report is a pure function of its inputs, and its inputs are cached for
    # `cache_ttl_seconds`; caching the report for the same window turns a
    # ~150 ms computation into a dictionary lookup without serving anything
    # staler than the data underneath it already was.
    app.state.reports = TTLCache(settings.cache_ttl_seconds,
                                 max_items=settings.cache_max_symbols)
    # Ledger writes happen once per symbol per session; after the first, the
    # unique keys make every further attempt a no-op, so skip the round-trips.
    app.state.ledger_done = TTLCache(6 * 3600, max_items=20_000)
    app.state.anthropic = anthropic_client
    if app.state.anthropic is None and settings.anthropic_api_key is not None:
        import anthropic

        app.state.anthropic = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), timeout=120.0, max_retries=2)
    app.state.agent_mode = "model" if app.state.anthropic is not None else "rules"

    app.add_middleware(SecurityMiddleware, settings=settings)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["GET", "POST"], allow_headers=["Authorization"])

    @app.middleware("http")
    async def docs_policy(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/docs":
            response.headers["Content-Security-Policy"] = DOCS_CSP
        return response

    def guard(request: Request) -> None:
        require_api_key_if_configured(request, settings)

    Authed = Depends(guard)

    def compute_budget(request: Request) -> None:
        """A tighter per-client limit for endpoints that cost real CPU."""
        allowed, retry = app.state.compute_limiter.allow(request.state.client_ip)
        if not allowed:
            raise HTTPException(status_code=429, detail="compute rate limit exceeded",
                                headers={"Retry-After": str(int(retry) + 1)})

    Heavy = Depends(compute_budget)

    def shareable(response: Response) -> None:
        """Let a CDN cache public analytics responses (opt-in, never with auth)."""
        if settings.public_cache_seconds and settings.api_key is None:
            response.headers["Cache-Control"] = (
                f"public, max-age={settings.public_cache_seconds}, "
                f"stale-while-revalidate={settings.public_cache_seconds}")

    Cacheable = Depends(shareable)
    def parse_symbol(raw: str) -> str:
        try:
            return normalise_symbol(raw)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid ticker symbol") from None

    def cached_report(symbol: str) -> tuple[dict, bytes]:
        """The report and its JSON encoding, both computed once per cache window.

        A report is ~90 KB of nested JSON; re-encoding it per request cost more
        than everything else on the hot path combined.
        """
        def build() -> tuple[dict, bytes]:
            data = report_builder.build(symbol, app.state.market, settings.benchmark_symbol)
            return data, json.dumps(data, separators=(",", ":"), default=str).encode()
        try:
            return app.state.reports.get_or_set(("report", symbol), build)
        except DataUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    def build_report(symbol: str) -> dict:
        return cached_report(symbol)[0]

    # -- pages -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/s/{symbol}", include_in_schema=False)
    def stock_page(symbol: str) -> FileResponse:
        parse_symbol(symbol)
        return FileResponse(WEB / "stock.html")

    @app.get("/architecture", include_in_schema=False)
    def architecture_page() -> FileResponse:
        return FileResponse(WEB / "architecture.html")

    @app.get("/why", include_in_schema=False)
    def why_page() -> FileResponse:
        return FileResponse(WEB / "why.html")

    @app.get("/track-record", include_in_schema=False)
    def track_record_page() -> FileResponse:
        return FileResponse(WEB / "track-record.html")

    @app.get("/portfolio", include_in_schema=False)
    def portfolio_page() -> FileResponse:
        return FileResponse(WEB / "portfolio.html")

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    # -- probes ------------------------------------------------------------

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        try:
            app.state.db.ping()
        except Exception:
            log.exception("readiness: database unreachable")
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    # -- API ---------------------------------------------------------------

    @app.get("/api/v1/meta", tags=["analytics"], dependencies=[Authed])
    def meta() -> dict:
        return {
            "version": __version__,
            "sources": app.state.market.sources,
            "benchmark": settings.benchmark_symbol,
            "weights": DEFAULT_WEIGHTS,
            "agent": {"mode": app.state.agent_mode,
                      "model": settings.agent_model if app.state.agent_mode == "model" else None,
                      "public": settings.agent_public},
            "demo": app.state.market.sources["prices"] == "demo",
        }

    @app.get("/api/v1/symbols/{symbol}/report", tags=["analytics"], dependencies=[Authed])
    def symbol_report(symbol: Symbol) -> Response:
        sym = parse_symbol(symbol)
        data, body = cached_report(sym)

        def record() -> bool:
            resolve_due(app.state.db, sym,
                        app.state.market.bars(sym, report_builder.HISTORY_DAYS))
            if app.state.db.forecasts_written_today() < settings.ledger_daily_limit:
                app.state.db.record_snapshot(data, app.state.market.sources["prices"])
                commit_forecast(sym, data)
            else:
                log.warning("ledger daily limit reached; nothing recorded",
                            extra={"symbol": log_safe(sym)})
            return True

        try:
            app.state.ledger_done.get_or_set((sym, data["quote"]["as_of"]), record)
        except Exception:
            # The ledger is valuable but not worth failing a page view over; a
            # failure is not cached, so the next view tries again.
            log.exception("could not record snapshot", extra={"symbol": log_safe(sym)})
        response = Response(content=body, media_type="application/json")
        shareable(response)
        return response

    def commit_forecast(sym: str, data: dict) -> None:
        """Write today's forecast once (due ones are resolved by the caller).

        Write-once in both directions: the first forecast of the session is the
        one that counts (a refresh cannot replace it), and an outcome, once
        recorded, is never revised. Gated by `ledger_daily_limit`.
        """
        if data.get("forecast"):
            app.state.db.record_forecast({**data["forecast"], "symbol": sym})

    def simulated_record(sym: str) -> dict:
        def build() -> dict:
            bars = app.state.market.bars(sym, report_builder.HISTORY_DAYS)
            try:
                bench = app.state.market.bars(settings.benchmark_symbol,
                                              report_builder.HISTORY_DAYS)
            except DataUnavailable:
                bench = None
            return backfill.symbol_record(bars, bench, symbol=sym)
        return app.state.records.get_or_set(("symbol", sym), build)

    @app.get("/api/v1/symbols/{symbol}/forecasts", tags=["track record"],
             dependencies=[Authed, Heavy])
    def symbol_forecasts(symbol: Symbol) -> dict:
        """The live ledger for one symbol, and its simulated walk-forward record."""
        sym = parse_symbol(symbol)
        try:
            sim = simulated_record(sym)
        except DataUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        live = app.state.db.forecasts(sym, limit=200)
        return {
            "symbol": sym,
            "live": {"kind": "live", "forecasts": live,
                     "scoring": forecast_scoring.score(live)},
            "simulated": {**sim, "forecasts": sim["forecasts"][-120:]},
        }

    @app.get("/api/v1/symbols/{symbol}/evidence", tags=["analytics"],
             dependencies=[Authed, Heavy, Cacheable])
    def symbol_evidence(symbol: Symbol) -> dict:
        """Walk-forward tests of every price family on this stock's own history.

        Separate from the report because it re-scores ~500 sessions (~0.5 s);
        cached for an hour, since the answer only changes with a new bar.
        """
        sym = parse_symbol(symbol)

        def build() -> dict | None:
            bars = app.state.market.bars(sym, report_builder.HISTORY_DAYS)
            try:
                bench = (app.state.market.bars(settings.benchmark_symbol,
                                               report_builder.HISTORY_DAYS)
                         if sym != settings.benchmark_symbol else None)
            except DataUnavailable:
                bench = None
            return test_families(bars, bench)
        try:
            result = app.state.records.get_or_set(("evidence", sym), build)
        except DataUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if result is None:
            raise HTTPException(status_code=404, detail="needs at least 300 sessions of history")
        return {"symbol": sym, **result}

    @app.get("/api/v1/track-record", tags=["track record"], dependencies=[Authed, Heavy])
    def track_record() -> dict:
        """Every forecast StockLens has committed, scored — and the simulated record.

        The live ledger is the record that cannot be cherry-picked: forecasts
        are written before their outcome exists. The simulated record walks the
        same model point-in-time over a fixed basket, so there is something to
        grade from day one; it is labelled as simulated everywhere it appears.
        """
        def build() -> dict:
            bench = app.state.market.bars(settings.benchmark_symbol, report_builder.HISTORY_DAYS)
            panel = {}
            for sym in TRACK_RECORD_UNIVERSE:
                try:
                    panel[sym] = app.state.market.bars(sym, report_builder.HISTORY_DAYS)
                except DataUnavailable:
                    continue
            return backfill.track_record(panel, bench)
        try:
            sim = app.state.records.get_or_set(("universe",), build)
        except DataUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        live = app.state.db.forecasts(limit=2000)
        return {
            "universe": list(TRACK_RECORD_UNIVERSE),
            "live": {"kind": "live", "count": len(live),
                     "scoring": forecast_scoring.score(live), "recent": live[:50]},
            "simulated": {k: v for k, v in sim.items() if k != "forecasts"},
            "disclaimer": "Educational analytics, not investment advice.",
        }

    @app.get("/api/v1/symbols/{symbol}/history", tags=["analytics"], dependencies=[Authed])
    def symbol_history(symbol: Symbol) -> dict:
        sym = parse_symbol(symbol)
        rows = app.state.db.snapshots(sym)
        for row in rows:
            row["created_at"] = row["created_at"].isoformat(timespec="seconds")
        return {"symbol": sym, "snapshots": rows}

    @app.get("/api/v1/portfolio", tags=["analytics"], dependencies=[Authed, Heavy, Cacheable])
    def portfolio(symbols: Annotated[str, Query(min_length=3, max_length=240)]) -> dict:
        """Correlation structure of a basket and five ways to weight it.

        Symbols are validated one by one before any upstream call, so the
        query string can never reach a provider URL unparsed. A symbol with no
        data is reported under `skipped` instead of failing the whole basket.
        """
        wanted = list(dict.fromkeys(parse_symbol(s) for s in symbols.split(",") if s.strip()))
        if not 2 <= len(wanted) <= portfolio_lab.MAX_SYMBOLS:
            raise HTTPException(status_code=422,
                                detail=f"give 2 to {portfolio_lab.MAX_SYMBOLS} symbols")
        closes, skipped = {}, {}
        for sym in wanted:
            try:
                closes[sym] = [b.close for b in app.state.market.bars(sym, 300)]
            except DataUnavailable as exc:
                skipped[sym] = str(exc)
        result = app.state.reports.get_or_set(
            ("portfolio", *closes), lambda: portfolio_lab.analyse(closes))
        if result is None:
            raise HTTPException(status_code=404,
                                detail="not enough aligned history for at least two symbols")
        dropped = {s: "too little usable history" for s in closes if s not in result["symbols"]}
        return {**result, "skipped": {**skipped, **dropped}, "sources": app.state.market.sources,
                "disclaimer": "Educational analytics, not investment advice."}

    @app.post("/api/v1/symbols/{symbol}/analyst", tags=["agent"], dependencies=[Authed])
    def analyst(symbol: Symbol, request: Request) -> dict:
        sym = parse_symbol(symbol)
        db: Database = app.state.db
        allowed, retry = app.state.agent_limiter.allow(request.state.client_ip)
        if not allowed:
            raise HTTPException(status_code=429, detail="analyst rate limit exceeded",
                                headers={"Retry-After": str(int(retry) + 1)})

        data = build_report(sym)
        note = None
        use_model = app.state.agent_mode == "model"
        if use_model and not (settings.agent_public or has_valid_key(request, settings)):
            use_model, note = False, "Model-written notes need an API key on this deployment."

        if use_model:
            cached = db.recent_agent_run(sym, settings.agent_cache_minutes)
            if cached:
                return to_response(cached, cached=True)
            if db.model_runs_today() >= settings.agent_daily_limit:
                use_model, note = False, "The daily model budget is spent; showing the rules brief."

        if use_model:
            result = run_model_agent(data, app.state.anthropic, settings.agent_model,
                                     settings.agent_effort, settings.agent_max_steps)
            if result.status != "ok":
                # Keep the failed run in the audit trail, then degrade gracefully.
                db.record_agent_run(**result.as_record(sym))
                note = f"The model run did not complete ({result.error}); showing the rules brief."
                result = rules_report(data)
        else:
            result = rules_report(data)
            note = note or "No model is configured; this brief is written by rules."

        db.record_agent_run(**result.as_record(sym))
        response = to_response(result)
        response["note"] = note
        return response

    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory stocklens.app:app_factory`."""
    return create_app()
