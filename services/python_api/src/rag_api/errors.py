"""Stable public error envelope and FastAPI exception handlers."""

from __future__ import annotations

from http import HTTPStatus
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

from .request_context import get_request_id


logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Expected application error that is safe to expose to API clients."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    error: ErrorDetail


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        request_id=get_request_id(request),
        error=ErrorDetail(code=code, message=message, details=details),
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _response(
        request,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=jsonable_encoder(exc.errors()),
    )


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    try:
        default_message = HTTPStatus(exc.status_code).phrase
    except ValueError:
        default_message = "HTTP error"
    message = exc.detail if isinstance(exc.detail, str) else default_message
    details = None if isinstance(exc.detail, str) else exc.detail
    return _response(
        request,
        status_code=exc.status_code,
        code="NOT_FOUND" if exc.status_code == HTTPStatus.NOT_FOUND else "HTTP_ERROR",
        message=message,
        details=details,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=exc)
    return _response(
        request,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
