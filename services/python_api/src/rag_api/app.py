"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .core_client import CoreClient, GrpcCoreClient
from .db.session import create_database_engine, create_session_factory
from .errors import register_exception_handlers
from .generation.runtime import build_answer_service
from .generation.service import AnswerService
from .observability import TelemetryRuntime, build_telemetry
from .request_context import request_context_middleware
from .routes.assets import router as assets_router
from .routes.health import router as health_router
from .routes.streaming import router as streaming_router
from .storage import S3ObjectStore
from .uploads import AssetUploadService, SqlAlchemyUploadRepository


def create_app(
    settings: Settings | None = None,
    core_client: CoreClient | None = None,
    upload_service: AssetUploadService | None = None,
    answer_service: AnswerService | None = None,
    telemetry: TelemetryRuntime | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_core_client = core_client or GrpcCoreClient(
        resolved_settings.core_grpc_target,
        resolved_settings.core_grpc_timeout_seconds,
        resolved_settings.core_grpc_index_timeout_seconds,
        resolved_settings.core_grpc_index_batch_max_bytes,
    )
    database_engine = None
    resolved_telemetry = telemetry or build_telemetry(resolved_settings)
    if upload_service is None:
        database_engine = create_database_engine(resolved_settings)
        repository = SqlAlchemyUploadRepository(
            create_session_factory(database_engine)
        )
        upload_service = AssetUploadService(
            settings=resolved_settings,
            repository=repository,
            object_store=S3ObjectStore(resolved_settings),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            try:
                await app.state.core_client.close()
                if database_engine is not None:
                    await database_engine.dispose()
            finally:
                await app.state.telemetry.shutdown()

    app = FastAPI(
        title="Multimodal RAG API",
        version=resolved_settings.service_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.core_client = resolved_core_client
    app.state.upload_service = upload_service
    app.state.answer_service = answer_service or build_answer_service(
        resolved_settings, resolved_core_client
    )
    app.state.telemetry = resolved_telemetry
    app.middleware("http")(request_context_middleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(assets_router, prefix=resolved_settings.api_prefix)
    app.include_router(streaming_router, prefix=resolved_settings.api_prefix)
    return app
