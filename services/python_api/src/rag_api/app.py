"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings
from .errors import register_exception_handlers
from .request_context import request_context_middleware
from .routes.health import router as health_router
from .routes.streaming import router as streaming_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    app = FastAPI(
        title="Multimodal RAG API",
        version=resolved_settings.service_version,
        debug=resolved_settings.debug,
    )
    app.state.settings = resolved_settings
    app.middleware("http")(request_context_middleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(streaming_router, prefix=resolved_settings.api_prefix)
    return app
