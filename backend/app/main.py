"""FastAPI application."""

import logging
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import api
from app.config import get_settings
from app.db import get_sessionmaker
from app.logging_config import configure_logging

log = logging.getLogger("app")

_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
          422: "validation_error", 429: "rate_limited", 503: "unavailable"}


def error_response(status: int, message: str, details: list | None = None) -> JSONResponse:
    """Every error leaves the API in this one shape."""
    body = {"error": {"code": _CODES.get(status, "error"), "message": message, "details": details or []}}
    return JSONResponse(body, status_code=status)


class RateLimiter:
    """Small in-memory sliding-window limiter per client IP (single process only)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        window = self.hits[client]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        if len(self.hits) > 10_000:  # bound memory
            self.hits.clear()
        return True


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(
        title="Football statistics and match probability API",
        version="0.1.0",
        description="Pre-match probability estimates and historical evaluation. Estimates are "
                    "uncertain and are not betting advice.",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.csv(settings.cors_origins), allow_methods=["GET", "POST"],
        allow_headers=["X-Admin-Token", "Content-Type"],
    )
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def rate_limit_and_log(request: Request, call_next):
        if settings.rate_limit_per_minute > 0 and request.url.path.startswith("/api/"):
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                return error_response(429, "Too many requests; please slow down")
        started = time.perf_counter()
        response = await call_next(request)
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return error_response(422, "Invalid request parameters", details)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        return error_response(500, "Internal server error")  # no trace leaves the server

    @app.get("/health", tags=["ops"])
    def health():
        """Liveness plus a database connectivity check."""
        try:
            with get_sessionmaker()() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            log.exception("health check: database unreachable")
            return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
        return {"status": "ok", "database": "ok"}

    app.include_router(api.router)
    app.include_router(api.admin)
    return app


app = create_app()
