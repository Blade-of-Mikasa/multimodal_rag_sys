"""Microsoft Foundry Grounding with Bing adapter.

The retired Bing Web Search API returned raw result objects.  Foundry Grounding
does not: it returns a model-produced answer plus URL citation annotations.  This
adapter deliberately exposes those citations as candidates and leaves page
download/extraction to the application-owned pipeline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
from typing import Any, AsyncIterator, Protocol
from urllib.parse import quote_plus, urldefrag, urlsplit

import aiohttp

from .domain import (
    SearchCitation,
    SearchProviderError,
    SearchQuery,
    SearchResponse,
)


class AccessTokenProvider(Protocol):
    async def get_token(self) -> str: ...


class StaticAccessTokenProvider:
    """Small deployment adapter; managed identity can implement the same port."""

    def __init__(self, access_token: str) -> None:
        if not access_token.strip():
            raise ValueError("Foundry access token must not be blank")
        self._access_token = access_token

    async def get_token(self) -> str:
        return self._access_token


class FoundryBingSearchProvider:
    provider_name = "microsoft_foundry_bing_grounding"

    def __init__(
        self,
        *,
        responses_url: str,
        model_deployment: str,
        project_connection_id: str,
        token_provider: AccessTokenProvider,
        timeout_seconds: float,
        default_market: str | None = None,
        default_language: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        parsed = urlsplit(responses_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Foundry responses URL must be an HTTPS URL")
        if not model_deployment.strip() or not project_connection_id.strip():
            raise ValueError("Foundry model and Bing connection must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("Foundry timeout must be positive")
        self._responses_url = responses_url.rstrip("/")
        self._model_deployment = model_deployment
        self._project_connection_id = project_connection_id
        self._token_provider = token_provider
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._default_market = default_market
        self._default_language = default_language
        self._session = session

    async def search(self, query: SearchQuery) -> SearchResponse:
        configuration: dict[str, Any] = {
            "project_connection_id": self._project_connection_id,
            "count": query.count,
        }
        market = query.market or self._default_market
        language = query.language or self._default_language
        if market:
            configuration["market"] = market
        if language:
            configuration["set_lang"] = language
        if query.freshness:
            configuration["freshness"] = query.freshness
        request = {
            "model": self._model_deployment,
            "input": query.text,
            "tool_choice": "required",
            "tools": [
                {
                    "type": "bing_grounding",
                    "bing_grounding": {
                        "search_configurations": [configuration]
                    },
                }
            ],
        }
        token = await self._token_provider.get_token()
        try:
            async with self._session_scope() as session:
                async with session.post(
                    self._responses_url,
                    json=request,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=self._timeout,
                ) as response:
                    if not 200 <= response.status < 300:
                        detail = (await response.text())[:500]
                        retryable = response.status in {408, 409, 425, 429} or (
                            response.status >= 500
                        )
                        raise SearchProviderError(
                            f"Foundry Bing returned {response.status}: {detail}",
                            retryable=retryable,
                        )
                    payload = await response.json(content_type=None)
        except SearchProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise SearchProviderError(
                f"Foundry Bing is unavailable: {type(error).__name__}",
                retryable=True,
            ) from error
        except ValueError as error:
            raise SearchProviderError(
                "Foundry Bing returned invalid JSON", retryable=False
            ) from error
        return _parse_response(
            payload, query.text, self.provider_name, max_results=query.count
        )

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[aiohttp.ClientSession]:
        if self._session is not None:
            yield self._session
            return
        async with aiohttp.ClientSession(trust_env=False) as session:
            yield session


def _parse_response(
    payload: Any, query: str, provider: str, *, max_results: int
) -> SearchResponse:
    if not isinstance(payload, dict):
        raise SearchProviderError(
            "Foundry Bing response must be an object", retryable=False
        )
    if payload.get("status") not in {None, "completed"}:
        raise SearchProviderError(
            f"Foundry Bing response is {payload.get('status')!r}",
            retryable=payload.get("status") in {"queued", "in_progress"},
        )
    outputs = payload.get("output")
    if not isinstance(outputs, list):
        raise SearchProviderError(
            "Foundry Bing response contains no output", retryable=False
        )

    grounded_parts: list[str] = []
    raw_citations: list[tuple[str, str, str]] = []
    search_urls: list[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        _collect_search_urls(item.get("arguments"), search_urls)
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if not isinstance(text, str):
                continue
            grounded_parts.append(text)
            annotations = part.get("annotations", [])
            if not isinstance(annotations, list):
                continue
            for annotation in annotations:
                if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if not _is_http_url(url):
                    continue
                title = annotation.get("title")
                if not isinstance(title, str):
                    title = urlsplit(url).hostname or url
                cited_text = _citation_text(text, annotation)
                raw_citations.append(
                    (url, title.strip()[:4_096], cited_text[:16_384])
                )

    grounded_text = "\n".join(part.strip() for part in grounded_parts if part.strip())
    deduplicated: list[SearchCitation] = []
    seen: set[str] = set()
    for url, title, cited_text in raw_citations:
        identity = urldefrag(url)[0]
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(
            SearchCitation(
                rank=len(deduplicated) + 1,
                url=url,
                title=title,
                cited_text=cited_text,
            )
        )
        if len(deduplicated) >= max_results:
            break
    if not deduplicated:
        raise SearchProviderError(
            "Foundry Bing response contains no URL citations", retryable=False
        )

    if not search_urls:
        search_urls.append(f"https://www.bing.com/search?q={quote_plus(query)}")
    return SearchResponse(
        provider=provider,
        query=query,
        citations=tuple(deduplicated),
        grounded_text=grounded_text,
        search_urls=tuple(dict.fromkeys(search_urls)),
    )


def _citation_text(text: str, annotation: dict[str, Any]) -> str:
    start = annotation.get("start_index")
    end = annotation.get("end_index")
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 0 <= start < end <= len(text)
    ):
        return text[start:end].strip()
    return ""


def _collect_search_urls(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if decoded is not None:
            _collect_search_urls(decoded, output)
        if _is_bing_search_url(value):
            output.append(value)
        return
    if isinstance(value, dict):
        for nested in value.values():
            _collect_search_urls(nested, output)
    elif isinstance(value, list):
        for nested in value:
            _collect_search_urls(nested, output)


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 16_384:
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not any(character in value for character in ("\r", "\n", "\x00"))
    )


def _is_bing_search_url(value: Any) -> bool:
    if not _is_http_url(value):
        return False
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    return (host == "bing.com" or host.endswith(".bing.com")) and parsed.path.startswith(
        "/search"
    )
