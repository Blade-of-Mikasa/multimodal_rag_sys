"""Request correlation helpers shared by routes and error handlers."""

from __future__ import annotations

from contextvars import ContextVar
import re
from uuid import uuid4

from fastapi import Request, Response


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_request_id_context: ContextVar[str | None] = ContextVar(
    "request_id", default=None
)


def normalize_request_id(candidate: str | None) -> str:
    """Keep safe caller IDs and replace malformed values with a UUID."""

    if candidate and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def get_request_id(request: Request) -> str:
    return request.state.request_id


def current_request_id() -> str | None:
    return _request_id_context.get()


async def request_context_middleware(request: Request, call_next) -> Response:
    request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    token = _request_id_context.set(request_id)
    try:
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        _request_id_context.reset(token)
