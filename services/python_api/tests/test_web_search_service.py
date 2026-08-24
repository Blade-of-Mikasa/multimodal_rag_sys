from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import unittest

from rag_api.web.domain import (
    ExtractedPage,
    ExtractionStatus,
    FetchedPage,
    SearchCitation,
    SearchQuery,
    SearchResponse,
    WebExtractionError,
    WebFetchError,
)
from rag_api.web.service import WebSearchService


class FakeSearchProvider:
    async def search(self, query: SearchQuery) -> SearchResponse:
        self.query = query
        return SearchResponse(
            provider="fake-search",
            query=query.text,
            search_urls=("https://search.example/?q=rag",),
            grounded_text="Grounded provider summary.",
            citations=(
                SearchCitation(1, "https://one.example/", "One", "claim one"),
                SearchCitation(2, "https://two.example/", "Two", "claim two"),
                SearchCitation(3, "https://three.example/", "Three", "claim three"),
            ),
        )


class FakeFetcher:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def fetch(self, url: str) -> FetchedPage:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if "two" in url:
                raise WebFetchError(
                    "HTTP_STATUS", "publisher unavailable", retryable=True
                )
            marker = "bad" if "three" in url else "good"
            return FetchedPage(
                requested_url=url,
                final_url=url,
                html=marker,
                content_type="text/html",
                fetched_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            )
        finally:
            self.active -= 1


class FakeExtractor:
    def extract(self, page: FetchedPage) -> ExtractedPage:
        if page.html == "bad":
            raise WebExtractionError("NO_MAIN_CONTENT", "empty")
        text = "full extracted article"
        return ExtractedPage(
            canonical_url=page.final_url,
            title="Extracted one",
            text=text,
            content_sha256=sha256(text.encode()).hexdigest(),
        )


class WebSearchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_provider_evidence_when_individual_pages_fail(self) -> None:
        fetcher = FakeFetcher()
        service = WebSearchService(
            search_provider=FakeSearchProvider(),
            page_fetcher=fetcher,
            page_extractor=FakeExtractor(),
            max_concurrency=2,
        )

        result = await service.search(SearchQuery("rag architecture", count=3))

        self.assertEqual((1, 2, 3), tuple(item.rank for item in result.sources))
        self.assertEqual(ExtractionStatus.FULL, result.sources[0].status)
        self.assertEqual("full extracted article", result.sources[0].text)
        self.assertEqual(ExtractionStatus.CITATION_ONLY, result.sources[1].status)
        self.assertEqual("claim two", result.sources[1].text)
        self.assertEqual("HTTP_STATUS", result.sources[1].failure_code)
        self.assertEqual(ExtractionStatus.CITATION_ONLY, result.sources[2].status)
        self.assertEqual("NO_MAIN_CONTENT", result.sources[2].failure_code)
        self.assertIsNotNone(result.sources[2].fetched_at)
        self.assertEqual("Grounded provider summary.", result.grounded_text)
        self.assertEqual(2, fetcher.max_active)


if __name__ == "__main__":
    unittest.main()
