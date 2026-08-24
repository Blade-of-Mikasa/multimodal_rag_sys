"""Authenticated POST-over-SSE transport for the answer pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from http import HTTPStatus
import logging
import re
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from rag_api.domain import Modality
from rag_api.errors import ApiError
from rag_api.generation import (
    AnswerPipelineError,
    AnswerPreferences,
    AnswerService,
    AnswerUpdate,
)
from rag_api.models import StreamEvent, StreamQueryRequest
from rag_api.request_context import get_request_id
from rag_api.routes.assets import RequestPrincipal, request_principal


router = APIRouter(tags=["query"])
LOGGER = logging.getLogger(__name__)
SAFE_ACL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MODALITIES = {
    "document": Modality.DOCUMENT,
    "image": Modality.IMAGE,
    "video": Modality.VIDEO,
}


def encode_sse(event: StreamEvent) -> str:
    return (
        f"id: {event.request_id}:{event.sequence}\n"
        f"event: {event.event}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


def readable_acl_ids(
    x_acl_ids: Annotated[
        str | None,
        Header(alias="X-ACL-IDs", max_length=12_900),
    ] = None,
) -> tuple[str, ...]:
    if x_acl_ids is None:
        return ()
    values = tuple(item.strip() for item in x_acl_ids.split(","))
    if (
        not values
        or len(values) > 100
        or any(not SAFE_ACL_ID.fullmatch(value) for value in values)
        or len(set(values)) != len(values)
    ):
        raise ApiError(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="X-ACL-IDs must contain up to 100 unique safe identifiers",
        )
    return values


def answer_service(request: Request) -> AnswerService:
    return request.app.state.answer_service


@router.post("/queries/stream", response_class=StreamingResponse)
async def stream_query(
    payload: StreamQueryRequest,
    request: Request,
    principal: Annotated[RequestPrincipal, Depends(request_principal)],
    allowed_acl_ids: Annotated[tuple[str, ...], Depends(readable_acl_ids)],
) -> StreamingResponse:
    request_id = get_request_id(request)
    conversation_id = payload.conversation_id or str(uuid4())
    preferences = AnswerPreferences(
        retrieval_scope=payload.retrieval_scope,
        modalities=tuple(MODALITIES[item] for item in payload.modalities),
    )

    async def events() -> AsyncIterator[str]:
        sequence = 0
        yield encode_sse(
            StreamEvent(
                event="accepted",
                request_id=request_id,
                sequence=sequence,
                data={
                    "conversation_id": conversation_id,
                    "retrieval_scope": payload.retrieval_scope,
                    "modalities": payload.modalities,
                },
            )
        )
        sequence += 1
        try:
            updates = answer_service(request).stream(
                request_id=request_id,
                query=payload.query,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                conversation_id=conversation_id,
                allowed_acl_ids=allowed_acl_ids,
                preferences=preferences,
            )
            async for update in _with_heartbeats(
                updates,
                interval_seconds=request.app.state.settings.sse_heartbeat_seconds,
            ):
                if update is None:
                    event = StreamEvent(
                        event="heartbeat",
                        request_id=request_id,
                        sequence=sequence,
                        data={},
                    )
                else:
                    event = StreamEvent(
                        event=update.event,
                        request_id=request_id,
                        sequence=sequence,
                        data=update.data,
                    )
                yield encode_sse(event)
                sequence += 1
        except asyncio.CancelledError:
            raise
        except AnswerPipelineError as error:
            yield encode_sse(
                StreamEvent(
                    event="error",
                    request_id=request_id,
                    sequence=sequence,
                    data={
                        "code": error.code,
                        "message": str(error),
                        "retryable": error.retryable,
                    },
                )
            )
        except Exception:
            LOGGER.exception("unexpected answer pipeline failure")
            yield encode_sse(
                StreamEvent(
                    event="error",
                    request_id=request_id,
                    sequence=sequence,
                    data={
                        "code": "INTERNAL_ERROR",
                        "message": "回答流程发生内部错误",
                        "retryable": False,
                    },
                )
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


async def _with_heartbeats(
    updates: AsyncIterator[AnswerUpdate],
    *,
    interval_seconds: float,
) -> AsyncIterator[AnswerUpdate | None]:
    iterator = updates.__aiter__()
    pending = asyncio.create_task(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval_seconds)
            if not done:
                yield None
                continue
            try:
                update = pending.result()
            except StopAsyncIteration:
                return
            yield update
            pending = asyncio.create_task(anext(iterator))
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
