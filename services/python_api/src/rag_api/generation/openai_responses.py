"""OpenAI-compatible Responses adapter behind the generic ChatModel port."""

from __future__ import annotations

from collections.abc import AsyncIterator
import codecs
from contextlib import asynccontextmanager
import json
from typing import Any

import aiohttp

from .domain import (
    ChatCompletion,
    ChatDelta,
    ChatModelError,
    ChatRequest,
    TokenUsage,
)


_MAX_SSE_EVENT_CHARS = 4_000_000


class OpenAIResponsesChatModel:
    def __init__(
        self,
        *,
        endpoint_url: str,
        model_id: str,
        model_version: str,
        timeout_seconds: float,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._model_id = model_id
        self._model_version = model_version
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._api_key = api_key
        self._session = session

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_version(self) -> str:
        return self._model_version

    async def complete(self, request: ChatRequest) -> ChatCompletion:
        try:
            async with self._session_scope() as session:
                async with session.post(
                    self._endpoint_url,
                    json=self._payload(request, stream=False),
                    headers=self._headers(),
                    timeout=self._timeout,
                ) as response:
                    await _require_success(response)
                    payload = await response.json(content_type=None)
        except ChatModelError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise ChatModelError(
                f"chat endpoint unavailable: {type(error).__name__}",
                retryable=True,
            ) from error
        try:
            text = _extract_output_text(payload)
            if not text:
                raise ValueError("chat response contains blank output text")
            return ChatCompletion(
                text=text,
                finish_reason=_finish_reason(payload),
                usage=_usage(payload.get("usage")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ChatModelError(
                f"invalid chat response: {error}", retryable=False
            ) from error

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatDelta]:
        completed = False
        try:
            async with self._session_scope() as session:
                async with session.post(
                    self._endpoint_url,
                    json=self._payload(request, stream=True),
                    headers={**self._headers(), "Accept": "text/event-stream"},
                    timeout=self._timeout,
                ) as response:
                    await _require_success(response)
                    async for payload in _iter_sse_json(response.content):
                        event_type = payload.get("type")
                        if event_type == "response.output_text.delta":
                            delta = payload.get("delta")
                            if not isinstance(delta, str):
                                raise ChatModelError(
                                    "chat stream delta must be text",
                                    retryable=False,
                                )
                            if delta:
                                yield ChatDelta(text=delta)
                        elif event_type in {
                            "response.completed",
                            "response.incomplete",
                        }:
                            completed = True
                            response_payload = payload.get("response", {})
                            try:
                                final_delta = ChatDelta(
                                    finish_reason=_finish_reason(response_payload),
                                    usage=_usage(response_payload.get("usage")),
                                )
                            except (TypeError, ValueError) as error:
                                raise ChatModelError(
                                    f"invalid chat stream response: {error}",
                                    retryable=False,
                                ) from error
                            yield final_delta
                        elif event_type in {
                            "response.failed",
                            "error",
                        }:
                            raise ChatModelError(
                                _stream_error_message(payload),
                                retryable=True,
                            )
        except ChatModelError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise ChatModelError(
                f"chat stream unavailable: {type(error).__name__}",
                retryable=True,
            ) from error
        if not completed:
            raise ChatModelError(
                "chat stream ended before response.completed", retryable=True
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "input": [
                {
                    "role": message.role,
                    "content": [
                        {"type": "input_text", "text": message.content}
                    ],
                }
                for message in request.messages
            ],
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.response_schema_name,
                    "strict": True,
                    "schema": request.response_schema,
                }
            }
        return payload

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[aiohttp.ClientSession]:
        if self._session is not None:
            yield self._session
            return
        async with aiohttp.ClientSession(trust_env=False) as session:
            yield session


async def _require_success(response: Any) -> None:
    if 200 <= response.status < 300:
        return
    detail = (await response.text())[:500]
    retryable = response.status in {408, 409, 425, 429} or response.status >= 500
    raise ChatModelError(
        f"chat endpoint returned {response.status}: {detail}",
        retryable=retryable,
    )


def _extract_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise TypeError("chat response must be an object")
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    texts: list[str] = []
    for output in payload.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if (
                isinstance(content, dict)
                and content.get("type") == "output_text"
                and isinstance(content.get("text"), str)
            ):
                texts.append(content["text"])
    if not texts:
        raise ValueError("chat response contains no output text")
    return "".join(texts)


def _finish_reason(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "completed"
    status = payload.get("status")
    if status == "incomplete":
        details = payload.get("incomplete_details", {})
        if isinstance(details, dict) and isinstance(details.get("reason"), str):
            return details["reason"]
    return status if isinstance(status, str) else "completed"


def _usage(payload: Any) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()
    input_tokens = payload.get("input_tokens", 0)
    output_tokens = payload.get("output_tokens", 0)
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        raise ValueError("chat usage tokens must be non-negative integers")
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


async def _iter_sse_json(content: Any) -> AsyncIterator[dict[str, Any]]:
    buffer = ""
    data_lines: list[str] = []
    decoder = codecs.getincrementaldecoder("utf-8")()
    async for raw_chunk in content:
        try:
            buffer += decoder.decode(raw_chunk)
        except UnicodeDecodeError as error:
            raise ChatModelError(
                "chat stream is not valid UTF-8", retryable=False
            ) from error
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            event = _consume_sse_line(line, data_lines)
            if event is not None:
                yield event
        if len(buffer) > _MAX_SSE_EVENT_CHARS:
            raise ChatModelError(
                "chat stream event exceeds its size limit",
                retryable=False,
            )
    try:
        buffer += decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise ChatModelError(
            "chat stream is not valid UTF-8", retryable=False
        ) from error
    if buffer:
        event = _consume_sse_line(buffer.rstrip("\r"), data_lines)
        if event is not None:
            yield event
    if data_lines:
        event = _decode_sse_data(data_lines)
        if event is not None:
            yield event


def _consume_sse_line(
    line: str, data_lines: list[str]
) -> dict[str, Any] | None:
    if line == "":
        if data_lines:
            return _decode_sse_data(data_lines)
        return None
    if line.startswith("data:"):
        data_lines.append(line[5:].lstrip())
        if sum(len(item) for item in data_lines) > _MAX_SSE_EVENT_CHARS:
            raise ChatModelError(
                "chat stream event exceeds its size limit",
                retryable=False,
            )
    return None


def _decode_sse_data(data_lines: list[str]) -> dict[str, Any] | None:
    payload = "\n".join(data_lines)
    data_lines.clear()
    if payload == "[DONE]":
        return None
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ChatModelError(
            "chat stream contains invalid JSON", retryable=False
        ) from error
    if not isinstance(decoded, dict):
        raise ChatModelError(
            "chat stream event must be an object", retryable=False
        )
    return decoded


def _stream_error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return f"chat stream failed: {error['message'][:500]}"
    return f"chat stream failed with event {payload.get('type', 'unknown')}"
