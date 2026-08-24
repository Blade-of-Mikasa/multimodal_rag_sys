"""Orchestrates search, bounded page extraction and per-source degradation."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from .domain import (
    ExtractionStatus,
    ExtractedPage,
    FetchedPage,
    SearchCitation,
    SearchProvider,
    SearchQuery,
    WebExtractionError,
    WebFetchError,
    WebSearchBundle,
    WebSource,
)


class WebPageFetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


class WebPageExtractor(Protocol):
    def extract(self, page: FetchedPage) -> ExtractedPage: ...


class WebSearchService:
    def __init__(
        self,
        *,
        search_provider: SearchProvider,
        page_fetcher: WebPageFetcher,
        page_extractor: WebPageExtractor,
        max_concurrency: int,
    ) -> None:
        if not 1 <= max_concurrency <= 32:
            raise ValueError("web extraction concurrency must be between 1 and 32")
        self._search_provider = search_provider
        self._page_fetcher = page_fetcher
        self._page_extractor = page_extractor
        self._max_concurrency = max_concurrency

    async def search(self, query: SearchQuery) -> WebSearchBundle:
        response = await self._search_provider.search(query)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def extract_one(citation: SearchCitation) -> WebSource:
            async with semaphore:
                return await self._extract_one(citation)

        sources = await asyncio.gather(
            *(extract_one(citation) for citation in response.citations[: query.count])
        )
        return WebSearchBundle(
            provider=response.provider,
            query=response.query,
            search_urls=response.search_urls,
            grounded_text=response.grounded_text,
            sources=tuple(sources),
        )

    async def _extract_one(self, citation: SearchCitation) -> WebSource:
        page: FetchedPage | None = None
        try:
            page = await self._page_fetcher.fetch(citation.url)
            extracted = await asyncio.to_thread(self._page_extractor.extract, page)
        except WebFetchError as error:
            return _citation_only(citation, error.code)
        except WebExtractionError as error:
            return _citation_only(
                citation,
                error.code,
                fetched_at=page.fetched_at if page is not None else None,
            )
        return WebSource(
            rank=citation.rank,
            url=extracted.canonical_url,
            title=extracted.title or citation.title,
            text=extracted.text,
            status=ExtractionStatus.FULL,
            fetched_at=page.fetched_at,
            content_sha256=extracted.content_sha256,
            published_time=extracted.published_time,
            modified_time=extracted.modified_time,
        )


def _citation_only(
    citation: SearchCitation,
    failure_code: str,
    *,
    fetched_at: datetime | None = None,
) -> WebSource:
    return WebSource(
        rank=citation.rank,
        url=citation.url,
        title=citation.title,
        text=citation.cited_text,
        status=ExtractionStatus.CITATION_ONLY,
        fetched_at=fetched_at,
        failure_code=failure_code,
    )
