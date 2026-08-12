"""Process liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..config import Settings
from ..core_client import CoreUnavailableError
from ..models import HealthResponse
from ..request_context import get_request_id


router = APIRouter(tags=["health"])


def _health_response(
    request: Request,
    *,
    ready: bool,
    core_available: bool | None = None,
) -> HealthResponse:
    settings: Settings = request.app.state.settings
    checks: dict[str, str] = {"python_api": "ok"}
    if core_available is not None:
        checks["rag_core"] = "ok" if core_available else "unavailable"
    return HealthResponse(
        service=settings.service_name,
        version=settings.service_version,
        environment=settings.environment,
        status="ok" if ready else "degraded",
        ready=ready,
        request_id=get_request_id(request),
        checks=checks,
    )


@router.get("/health/live", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    return _health_response(request, ready=True)


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(request: Request) -> HealthResponse | JSONResponse:
    try:
        core_health = await request.app.state.core_client.health()
        if core_health.ready:
            return _health_response(
                request,
                ready=True,
                core_available=True,
            )
    except CoreUnavailableError:
        pass

    response = _health_response(
        request,
        ready=False,
        core_available=False,
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=jsonable_encoder(response.model_dump()),
    )
