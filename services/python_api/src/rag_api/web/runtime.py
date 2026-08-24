"""Configuration wiring kept separate from provider-neutral web contracts."""

from __future__ import annotations

from rag_api.config import Settings

from .bing import (
    AccessTokenProvider,
    FoundryBingSearchProvider,
    StaticAccessTokenProvider,
)
from .extractor import TrafilaturaPageExtractor
from .fetcher import SafeWebFetcher
from .service import WebSearchService


def build_web_search_service(
    settings: Settings,
    *,
    token_provider: AccessTokenProvider | None = None,
) -> WebSearchService:
    missing = [
        name
        for name, value in (
            ("bing_foundry_responses_url", settings.bing_foundry_responses_url),
            ("bing_foundry_model_deployment", settings.bing_foundry_model_deployment),
            ("bing_grounding_connection_id", settings.bing_grounding_connection_id),
        )
        if value is None
    ]
    if missing:
        raise ValueError(f"Bing Grounding configuration is missing: {', '.join(missing)}")
    if token_provider is None:
        if settings.bing_foundry_access_token is None:
            raise ValueError(
                "Bing Grounding requires a token provider or bing_foundry_access_token"
            )
        token_provider = StaticAccessTokenProvider(
            settings.bing_foundry_access_token.get_secret_value()
        )
    provider = FoundryBingSearchProvider(
        responses_url=settings.bing_foundry_responses_url,
        model_deployment=settings.bing_foundry_model_deployment,
        project_connection_id=settings.bing_grounding_connection_id,
        token_provider=token_provider,
        timeout_seconds=settings.web_search_timeout_seconds,
        default_market=settings.bing_default_market,
        default_language=settings.bing_default_language,
    )
    return WebSearchService(
        search_provider=provider,
        page_fetcher=SafeWebFetcher(
            timeout_seconds=settings.web_fetch_timeout_seconds,
            max_response_bytes=settings.web_fetch_max_bytes,
            max_redirects=settings.web_fetch_max_redirects,
            user_agent=settings.web_fetch_user_agent,
        ),
        page_extractor=TrafilaturaPageExtractor(
            max_text_chars=settings.web_extract_max_chars
        ),
        max_concurrency=settings.web_extract_max_concurrency,
    )
