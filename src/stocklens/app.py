"""The HTTP service: a small read-only API and the static UI that uses it.

    GET  /s/{SYMBOL}                         the stock page
    GET  /architecture                       how it is built, in the app itself
    GET  /api/v1/meta                        version, data sources, agent mode
    GET  /api/v1/symbols/{symbol}/report     everything the page shows
    GET  /api/v1/symbols/{symbol}/history    the recorded composite ledger
    POST /api/v1/symbols/{symbol}/analyst    the AI analyst note
    GET  /healthz  /readyz                   liveness and readiness probes

There is no write endpoint for market state, no account, no order path.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent.analyst import rules_report, run_model_agent, to_response
from .analytics import report as report_builder
from .analytics.composite import DEFAULT_WEIGHTS
from .config import Settings, get_settings
from .data import DataUnavailable, MarketData, build_market_data, normalise_symbol
from .db import Database
from .logging_setup import configure
from .security import RateLimiter, SecurityMiddleware, has_valid_key, require_api_key_if_configured

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"

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
    def parse_symbol(raw: str) -> str:
        try:
            return normalise_symbol(raw)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid ticker symbol") from None

    def build_report(symbol: str) -> dict:
        try:
            return report_builder.build(symbol, app.state.market, settings.benchmark_symbol)
        except DataUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

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
    def symbol_report(symbol: Symbol) -> dict:
        sym = parse_symbol(symbol)
        data = build_report(sym)
        try:
            app.state.db.record_snapshot(data, app.state.market.sources["prices"])
        except Exception:
            # The ledger is valuable but not worth failing a page view over.
            log.exception("could not record snapshot", extra={"symbol": sym})
        return data

    @app.get("/api/v1/symbols/{symbol}/history", tags=["analytics"], dependencies=[Authed])
    def symbol_history(symbol: Symbol) -> dict:
        sym = parse_symbol(symbol)
        rows = app.state.db.snapshots(sym)
        for row in rows:
            row["created_at"] = row["created_at"].isoformat(timespec="seconds")
        return {"symbol": sym, "snapshots": rows}

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
