"""SAMPADA AI - FastAPI application.

Run from the ``backend`` directory:

    uvicorn app.main:app --reload --port 8000

Interactive docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.db import db_path, init_db, seed_if_empty
from .core.errors import ApiError
from .core.i18n import supported_languages

logger = logging.getLogger("sampada")

PROJECT_NAME = settings.require("project.name")
PROJECT_VERSION = settings.require("project.version")
API_PREFIX = str(settings.get("backend.api_prefix", "/api/v1"))


def ensure_database() -> None:
    """Create the schema if it is missing. Idempotent and safe to call twice.

    Called at import as well as from lifespan: ASGI test clients and some
    deployment shims do not run lifespan handlers, and serving a request against
    a missing schema would fail in a confusing way rather than at startup.
    """
    init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_database()
    seed_if_empty()
    path = db_path()
    logger.info("SAMPADA AI %s starting; database at %s", PROJECT_VERSION, path)
    if settings.sample_mode:
        logger.warning(
            "data.mode = 'sample': DEMO DATA is active. Responses derived from it "
            "are tagged is_demo=true and must not be shown as real farmer data."
        )
    yield


app = FastAPI(
    title=PROJECT_NAME,
    version=str(PROJECT_VERSION),
    description=(
        "Community-Aware AI Agricultural Decision Support System.\n\n"
        "**This system returns estimates and confidence levels, never guarantees.** "
        "Where a data source is unavailable it says so explicitly rather than "
        "guessing. See /api/v1/providers/status for which sources are live."
    ),
    lifespan=lifespan,
)

ensure_database()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.get("backend.cors_origins", ["*"])),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Error handling: one consistent envelope for every failure
# ---------------------------------------------------------------------------
@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_failed",
                "message": "the request body or parameters failed validation",
                "details": exc.errors(),
            }
        },
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
from .api import (  # noqa: E402  (imported after app creation, as before)
    auth,
    evaluation,
    farmer,
    health,
    market,
    outcomes,
    recommendation,
    soil,
    weather,
)

for module in (health, auth, farmer, soil, weather, market, recommendation, outcomes, evaluation):
    app.include_router(module.router, prefix=API_PREFIX)


# API console: a read-mostly browser client that calls the live API and renders
# every response verbatim. It adds no data of its own - unavailable sources are
# shown exactly as the API reports them. Static only; nothing here can shadow
# the versioned routes above (they claim /api/v1/*, this claims /console).
_CONSOLE_DIR = Path(__file__).resolve().parent / "console"
app.mount(
    "/console",
    StaticFiles(directory=_CONSOLE_DIR, html=True),
    name="console",
)

# Farmer-facing frontend: the same API, rendered as values only - labels instead
# of codes, dropdowns filled from /enums and /crops, and unavailable sources
# shown as explicit notices. Also static; it claims /app.
_APPUI_DIR = Path(__file__).resolve().parent / "appui"
app.mount(
    "/app",
    StaticFiles(directory=_APPUI_DIR, html=True),
    name="appui",
)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, object]:
    return {
        "name": PROJECT_NAME,
        "version": PROJECT_VERSION,
        "api_prefix": API_PREFIX,
        "docs": "/docs",
        "console": "/console",
        "app": "/app",
        "health": f"{API_PREFIX}/health",
        "supported_languages": supported_languages(),
        "notice": (
            "Decision support only. Outputs are estimates with confidence levels "
            "and are not guarantees of price, yield or profit."
        ),
    }


@app.get("/health", include_in_schema=False)
async def bare_health() -> dict[str, str]:
    """Unversioned liveness probe for container orchestrators."""
    return {"status": "ok"}


__all__ = ["app", "db_path"]
