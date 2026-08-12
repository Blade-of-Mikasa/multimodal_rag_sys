"""Process liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import Settings
from ..models import HealthResponse
from ..request_context import get_request_id


router = APIRouter(tags=["health"])


def _health_response(request: Request, *, ready: bool) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        service=settings.service_name,
        version=settings.service_version,
        environment=settings.environment,
        ready=ready,
        request_id=get_request_id(request),
        checks={"python_api": "ok"},
    )


@router.get("/health/live", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    return _health_response(request, ready=True)


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(request: Request) -> HealthResponse:
    # M03 will add the C++ core connectivity check to readiness.
    return _health_response(request, ready=True)
