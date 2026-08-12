"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .core_client import CoreClient, GrpcCoreClient
from .errors import register_exception_handlers
from .request_context import request_context_middleware
from .routes.health import router as health_router
from .routes.streaming import router as streaming_router


def create_app(
    settings: Settings | None = None,
    core_client: CoreClient | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.core_client.close()

    app = FastAPI(
        title="Multimodal RAG API",
        version=resolved_settings.service_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.core_client = core_client or GrpcCoreClient(
        resolved_settings.core_grpc_target,
        resolved_settings.core_grpc_timeout_seconds,
    )
    app.middleware("http")(request_context_middleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(streaming_router, prefix=resolved_settings.api_prefix)
    return app
