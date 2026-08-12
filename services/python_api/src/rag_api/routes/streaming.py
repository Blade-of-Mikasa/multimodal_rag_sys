"""SSE transport skeleton for the future retrieval and generation pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..models import StreamEvent, StreamQueryRequest
from ..request_context import get_request_id


router = APIRouter(tags=["query"])


def encode_sse(event: StreamEvent) -> str:
    return (
        f"id: {event.request_id}:{event.sequence}\n"
        f"event: {event.event}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


@router.post("/queries/stream", response_class=StreamingResponse)
async def stream_query(
    payload: StreamQueryRequest, request: Request
) -> StreamingResponse:
    request_id = get_request_id(request)

    async def events() -> AsyncIterator[str]:
        yield encode_sse(
            StreamEvent(
                event="accepted",
                request_id=request_id,
                sequence=0,
                data={"query_received": True},
            )
        )
        await asyncio.sleep(0)
        yield encode_sse(
            StreamEvent(
                event="done",
                request_id=request_id,
                sequence=1,
                data={"answer": "", "finish_reason": "pipeline_not_connected"},
            )
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
