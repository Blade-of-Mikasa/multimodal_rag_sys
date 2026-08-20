from __future__ import annotations

import unittest

from rag_api.web.bing import FoundryBingSearchProvider, StaticAccessTokenProvider
from rag_api.web.domain import SearchProviderError, SearchQuery


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


class FoundryBingSearchProviderTest(unittest.IsolatedAsyncioTestCase):
    def provider(self, session: FakeSession) -> FoundryBingSearchProvider:
        return FoundryBingSearchProvider(
            responses_url="https://project.ai.azure.com/openai/v1/responses",
            model_deployment="grounding-model",
            project_connection_id="/subscriptions/s/projects/p/connections/bing",
            token_provider=StaticAccessTokenProvider("azure-token"),
            timeout_seconds=12,
            default_market="en-US",
            default_language="en",
            session=session,
        )

    async def test_maps_grounded_citations_without_assuming_raw_bing_results(self) -> None:
        answer = "Alpha evidence supports the result."
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "bing_grounding_call",
                            "arguments": '{"search_url":"https://www.bing.com/search?q=alpha"}',
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": answer,
                                    "annotations": [
                                        {
                                            "type": "url_citation",
                                            "url": "https://source.example/article#part",
                                            "title": "Source article",
                                            "start_index": 0,
                                            "end_index": 14,
                                        },
                                        {
                                            "type": "url_citation",
                                            "url": "https://source.example/article#other",
                                            "title": "Duplicate fragment",
                                        },
                                    ],
                                }
                            ],
                        },
                    ],
                },
            )
        )

        result = await self.provider(session).search(
            SearchQuery("alpha", count=7, freshness="week")
        )

        self.assertEqual("microsoft_foundry_bing_grounding", result.provider)
        self.assertEqual(1, len(result.citations))
        self.assertEqual("Alpha evidence", result.citations[0].cited_text)
        self.assertEqual(
            ("https://www.bing.com/search?q=alpha",), result.search_urls
        )
        request = session.call[1]
        self.assertEqual("Bearer azure-token", request["headers"]["Authorization"])
        configuration = request["json"]["tools"][0]["bing_grounding"][
            "search_configurations"
        ][0]
        self.assertEqual(
            {
                "project_connection_id": "/subscriptions/s/projects/p/connections/bing",
                "count": 7,
                "market": "en-US",
                "set_lang": "en",
                "freshness": "week",
            },
            configuration,
        )
        self.assertEqual("required", request["json"]["tool_choice"])

    async def test_http_and_missing_citations_preserve_retry_semantics(self) -> None:
        with self.assertRaises(SearchProviderError) as retryable:
            await self.provider(FakeSession(FakeResponse(429, {}, "busy"))).search(
                SearchQuery("query")
            )
        self.assertTrue(retryable.exception.retryable)

        with self.assertRaises(SearchProviderError) as permanent:
            await self.provider(
                FakeSession(
                    FakeResponse(
                        200,
                        {
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [
                                        {"type": "output_text", "text": "uncited"}
                                    ],
                                }
                            ],
                        },
                    )
                )
            ).search(SearchQuery("query"))
        self.assertFalse(permanent.exception.retryable)


if __name__ == "__main__":
    unittest.main()
