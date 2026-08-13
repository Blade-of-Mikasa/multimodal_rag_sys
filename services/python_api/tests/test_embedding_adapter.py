from __future__ import annotations

import unittest

from rag_api.documents.domain import EmbeddingError
from rag_api.documents.embeddings import HttpEmbeddingModel


class FakeResponse:
    def __init__(self, status: int, payload: object, text: str = "") -> None:
        self.status = status
        self.payload = payload
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


class EmbeddingAdapterTest(unittest.IsolatedAsyncioTestCase):
    def adapter(self, session: FakeSession) -> HttpEmbeddingModel:
        return HttpEmbeddingModel(
            endpoint_url="https://models.example/v1/embeddings",
            api_key="secret",
            model_id="generic-embedding",
            model_version="2026-08",
            dimension=2,
            timeout_seconds=3,
            session=session,
        )

    async def test_reorders_openai_compatible_response_by_index(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                },
            )
        )

        vectors = await self.adapter(session).embed(("first", "second"))

        self.assertEqual(((1.0, 0.0), (0.0, 1.0)), vectors)
        self.assertEqual(
            "Bearer secret", session.call[1]["headers"]["Authorization"]
        )
        self.assertEqual(
            {"model": "generic-embedding", "input": ["first", "second"]},
            session.call[1]["json"],
        )

    async def test_http_and_contract_errors_keep_retry_semantics(self) -> None:
        with self.assertRaises(EmbeddingError) as retryable:
            await self.adapter(FakeSession(FakeResponse(429, {}, "busy"))).embed(
                ("text",)
            )
        self.assertTrue(retryable.exception.retryable)

        with self.assertRaises(EmbeddingError) as permanent:
            await self.adapter(
                FakeSession(
                    FakeResponse(
                        200, {"data": [{"index": 0, "embedding": [1.0]}]}
                    )
                )
            ).embed(("text",))
        self.assertFalse(permanent.exception.retryable)


if __name__ == "__main__":
    unittest.main()
