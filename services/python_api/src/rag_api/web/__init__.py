"""Provider-neutral web search and extraction pipeline."""

from .domain import (
    ExtractionStatus,
    ExtractedPage,
    FetchedPage,
    SearchCitation,
    SearchProvider,
    SearchProviderError,
    SearchQuery,
    SearchResponse,
    SourceTime,
    SourceTimeKind,
    WebExtractionError,
    WebFetchError,
    WebSearchBundle,
    WebSource,
)
from .service import WebSearchService
from .evidence import web_bundle_to_evidence

__all__ = [
    "ExtractionStatus",
    "ExtractedPage",
    "FetchedPage",
    "SearchCitation",
    "SearchProvider",
    "SearchProviderError",
    "SearchQuery",
    "SearchResponse",
    "SourceTime",
    "SourceTimeKind",
    "WebExtractionError",
    "WebFetchError",
    "WebSearchBundle",
    "WebSearchService",
    "WebSource",
    "web_bundle_to_evidence",
]
