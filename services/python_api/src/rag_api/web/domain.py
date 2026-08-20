"""Stable contracts shared by search, fetching and evidence construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Protocol
from urllib.parse import urlsplit


_FRESHNESS = re.compile(
    r"^(day|week|month|\d{4}-\d{2}-\d{2}(?:\.\.\d{4}-\d{2}-\d{2})?)$",
    re.IGNORECASE,
)


class SearchProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class WebFetchError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class WebExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SourceTimeKind(StrEnum):
    PUBLISHED = "published"
    MODIFIED = "modified"


class ExtractionStatus(StrEnum):
    FULL = "full"
    CITATION_ONLY = "citation_only"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    count: int = 5
    market: str | None = None
    language: str | None = None
    freshness: str | None = None

    def __post_init__(self) -> None:
        normalized = self.text.strip()
        if not normalized or len(normalized) > 2_048 or "\x00" in normalized:
            raise ValueError("search query must contain 1 to 2048 safe characters")
        if not 1 <= self.count <= 50:
            raise ValueError("search result count must be between 1 and 50")
        for name, value in (("market", self.market), ("language", self.language)):
            if value is not None and (
                not value.strip() or len(value) > 32 or not value.isascii()
            ):
                raise ValueError(f"search {name} must be a short ASCII code")
            if value is not None:
                object.__setattr__(self, name, value.strip())
        if self.freshness is not None and not _FRESHNESS.fullmatch(
            self.freshness
        ):
            raise ValueError("search freshness must be day, week, month or a date range")
        object.__setattr__(self, "text", normalized)


@dataclass(frozen=True, slots=True)
class SearchCitation:
    rank: int
    url: str
    title: str
    cited_text: str = ""

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("citation rank must be positive")
        _require_http_url(self.url, "citation URL")
        if len(self.title) > 4_096 or len(self.cited_text) > 16_384:
            raise ValueError("citation metadata exceeds its size limit")


@dataclass(frozen=True, slots=True)
class SearchResponse:
    provider: str
    query: str
    citations: tuple[SearchCitation, ...]
    grounded_text: str = ""
    search_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.query.strip():
            raise ValueError("search provider and query must not be blank")
        if len(self.citations) > 50:
            raise ValueError("search response must not contain more than 50 citations")
        if tuple(item.rank for item in self.citations) != tuple(
            range(1, len(self.citations) + 1)
        ):
            raise ValueError("citation ranks must be contiguous and ordered")
        for url in self.search_urls:
            _require_http_url(url, "provider search URL")


class SearchProvider(Protocol):
    async def search(self, query: SearchQuery) -> SearchResponse: ...


@dataclass(frozen=True, slots=True)
class FetchedPage:
    requested_url: str
    final_url: str
    html: str
    content_type: str
    fetched_at: datetime
    http_last_modified: datetime | None = None

    def __post_init__(self) -> None:
        _require_http_url(self.requested_url, "requested URL")
        _require_http_url(self.final_url, "final URL")
        _require_aware(self.fetched_at, "fetched_at")
        if self.http_last_modified is not None:
            _require_aware(self.http_last_modified, "http_last_modified")


@dataclass(frozen=True, slots=True)
class SourceTime:
    kind: SourceTimeKind
    value: datetime
    source: str
    precision: str
    timezone_assumed: bool = False
    raw_value: str = ""

    def __post_init__(self) -> None:
        _require_aware(self.value, "source time")
        if self.precision not in {"date", "second"}:
            raise ValueError("source time precision must be date or second")
        if not self.source.strip():
            raise ValueError("source time provenance must not be blank")


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    canonical_url: str
    title: str
    text: str
    content_sha256: str
    published_time: SourceTime | None = None
    modified_time: SourceTime | None = None

    def __post_init__(self) -> None:
        _require_http_url(self.canonical_url, "canonical URL")
        if not self.text.strip():
            raise ValueError("extracted page text must not be blank")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("extracted content hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class WebSource:
    rank: int
    url: str
    title: str
    text: str
    status: ExtractionStatus
    fetched_at: datetime | None = None
    content_sha256: str | None = None
    published_time: SourceTime | None = None
    modified_time: SourceTime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("web source rank must be positive")
        _require_http_url(self.url, "web source URL")
        if self.fetched_at is not None:
            _require_aware(self.fetched_at, "web source fetched_at")
        if self.content_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.content_sha256
        ):
            raise ValueError("web source content hash must be lowercase SHA-256")
        if self.status is ExtractionStatus.FULL:
            if not self.text.strip() or self.fetched_at is None or self.content_sha256 is None:
                raise ValueError("full web source requires text, fetch time and content hash")
            if self.failure_code is not None:
                raise ValueError("full web source must not contain a failure code")
        elif not self.failure_code:
            raise ValueError("citation-only web source requires a failure code")


@dataclass(frozen=True, slots=True)
class WebSearchBundle:
    provider: str
    query: str
    search_urls: tuple[str, ...]
    grounded_text: str
    sources: tuple[WebSource, ...]

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.query.strip():
            raise ValueError("web search provider and query must not be blank")
        for url in self.search_urls:
            _require_http_url(url, "provider search URL")
        if tuple(item.rank for item in self.sources) != tuple(
            range(1, len(self.sources) + 1)
        ):
            raise ValueError("web source ranks must be contiguous and ordered")


def _require_http_url(value: str, field: str) -> None:
    if len(value) > 16_384 or any(character in value for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{field} must be an HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as error:
        raise ValueError(f"{field} must be an HTTP(S) URL") from error
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{field} must be an HTTP(S) URL without credentials")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
