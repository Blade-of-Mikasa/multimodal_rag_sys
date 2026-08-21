from __future__ import annotations

import unittest

from rag_api.generation import (
    ChatMessage,
    ChatModelError,
    ChatRequest,
    OpenAIResponsesChatModel,
)


class AsyncChunks:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk

        return iterate()


class FakeResponse:
    def __init__(
        self,
        status: int,
        payload: object,
        *,
        chunks: list[bytes] | None = None,
        text: str = "",
    ) -> None:
        self.status = status
        self.payload = payload
        self.content = AsyncChunks(chunks or [])
        self.body_text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass

    async def json(self, *, content_type=None):
        return self.payload

    async def text(self) -> str:
        return self.body_text


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.call = None

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.call = (url, kwargs)
        return self.response


class ChatModelTest(unittest.IsolatedAsyncioTestCase):
    def model(self, session: FakeSession) -> OpenAIResponsesChatModel:
        return OpenAIResponsesChatModel(
            endpoint_url="https://models.example/v1/responses",
            api_key="secret",
            model_id="chat-general",
            model_version="2026-08",
            timeout_seconds=5,
            session=session,
        )

    async def test_complete_uses_strict_schema_and_model_identity(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "output_text": '{"routes":[]}',
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                },
            )
        )
        request = ChatRequest(
            messages=(ChatMessage("user", "plan"),),
            max_output_tokens=100,
            response_schema_name="plan",
            response_schema={"type": "object"},
        )

        result = await self.model(session).complete(request)

        self.assertEqual('{"routes":[]}', result.text)
        self.assertEqual(3, result.usage.output_tokens)
        body = session.call[1]["json"]
        self.assertEqual("chat-general", body["model"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual("Bearer secret", session.call[1]["headers"]["Authorization"])

    async def test_stream_handles_arbitrary_utf8_chunks_and_usage(self) -> None:
        payload = (
            'data: {"type":"response.output_text.delta","delta":"你好"}\n\n'
            'data: {"type":"response.completed","response":{"status":"completed",'
            '"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'
        ).encode()
        split = payload.index("你".encode()) + 1
        session = FakeSession(
            FakeResponse(200, {}, chunks=[payload[:split], payload[split:]])
        )

        deltas = [
            delta
            async for delta in self.model(session).stream(
                ChatRequest((ChatMessage("user", "answer"),), 100)
            )
        ]

        self.assertEqual("你好", deltas[0].text)
        self.assertEqual("completed", deltas[1].finish_reason)
        self.assertEqual(4, deltas[1].usage.input_tokens)

    async def test_http_error_preserves_retry_semantics(self) -> None:
        session = FakeSession(FakeResponse(429, {}, text="busy"))
        with self.assertRaises(ChatModelError) as caught:
            await self.model(session).complete(
                ChatRequest((ChatMessage("user", "answer"),), 100)
            )
        self.assertTrue(caught.exception.retryable)

    async def test_incomplete_stream_is_a_valid_terminal_reason(self) -> None:
        payload = (
            'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
            'data: {"type":"response.incomplete","response":'
            '{"status":"incomplete","incomplete_details":'
            '{"reason":"max_output_tokens"}}}\n\n'
        ).encode()
        session = FakeSession(FakeResponse(200, {}, chunks=[payload]))

        deltas = [
            delta
            async for delta in self.model(session).stream(
                ChatRequest((ChatMessage("user", "answer"),), 100)
            )
        ]

        self.assertEqual("partial", deltas[0].text)
        self.assertEqual("max_output_tokens", deltas[1].finish_reason)


if __name__ == "__main__":
    unittest.main()
