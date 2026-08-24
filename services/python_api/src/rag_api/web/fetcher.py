"""Bounded HTML fetcher with redirect-aware SSRF protection."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import socket
from typing import AsyncIterator, Callable
from urllib.parse import urldefrag, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

from .domain import FetchedPage, WebFetchError


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HTML_MEDIA_TYPES = {"text/html", "application/xhtml+xml"}


class UnsafeAddressError(OSError):
    pass


class PublicUrlPolicy:
    def __init__(self, *, allowed_ports: frozenset[int] = frozenset({80, 443})) -> None:
        self._allowed_ports = allowed_ports

    def validate(self, url: str) -> str:
        if len(url) > 16_384 or any(character in url for character in ("\r", "\n", "\x00")):
            raise WebFetchError(
                "URL_REJECTED", "web URL has invalid length or characters", retryable=False
            )
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as error:
            raise WebFetchError(
                "URL_REJECTED", "web URL contains an invalid port", retryable=False
            ) from error
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise WebFetchError(
                "URL_REJECTED", "only absolute HTTP(S) web URLs are allowed", retryable=False
            )
        if parsed.username is not None or parsed.password is not None:
            raise WebFetchError(
                "URL_REJECTED", "web URLs must not contain credentials", retryable=False
            )
        effective_port = port or (443 if scheme == "https" else 80)
        if effective_port not in self._allowed_ports:
            raise WebFetchError(
                "URL_REJECTED", "web URL port is not allowed", retryable=False
            )
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise WebFetchError(
                "SSRF_BLOCKED", "local web host is not allowed", retryable=False
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not _is_public_address(address):
            raise WebFetchError(
                "SSRF_BLOCKED", "non-public web address is not allowed", retryable=False
            )
        return urldefrag(url)[0]


class PublicNetworkResolver(AbstractResolver):
    """Reject a hostname if any DNS answer can reach a non-public network."""

    def __init__(self, delegate: AbstractResolver | None = None) -> None:
        self._delegate = delegate or DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        answers = await self._delegate.resolve(host, port, family)
        if not answers:
            raise OSError("DNS returned no addresses")
        for answer in answers:
            try:
                address = ipaddress.ip_address(answer["host"])
            except ValueError as error:
                raise UnsafeAddressError("DNS returned a non-IP address") from error
            if not _is_public_address(address):
                raise UnsafeAddressError("DNS resolved to a non-public address")
        return answers

    async def close(self) -> None:
        await self._delegate.close()


class SafeWebFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        max_redirects: int,
        user_agent: str,
        url_policy: PublicUrlPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("web fetch timeout and byte limit must be positive")
        if not 0 <= max_redirects <= 10:
            raise ValueError("web redirect limit must be between 0 and 10")
        if not user_agent.strip() or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("web user agent must be a non-empty single line")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._url_policy = url_policy or PublicUrlPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._session = session

    async def fetch(self, url: str) -> FetchedPage:
        requested_url = self._url_policy.validate(url)
        current_url = requested_url
        visited: set[str] = set()
        try:
            async with self._session_scope() as session:
                for redirect_count in range(self._max_redirects + 1):
                    current_url = self._url_policy.validate(current_url)
                    if current_url in visited:
                        raise WebFetchError(
                            "REDIRECT_LOOP", "web redirect loop detected", retryable=False
                        )
                    visited.add(current_url)
                    async with session.get(
                        current_url,
                        allow_redirects=False,
                        headers={
                            "User-Agent": self._user_agent,
                            "Accept": "text/html,application/xhtml+xml",
                        },
                        timeout=self._timeout,
                    ) as response:
                        if response.status in _REDIRECT_STATUSES:
                            if redirect_count >= self._max_redirects:
                                raise WebFetchError(
                                    "TOO_MANY_REDIRECTS",
                                    "web page exceeded the redirect limit",
                                    retryable=False,
                                )
                            location = response.headers.get("Location")
                            if not location:
                                raise WebFetchError(
                                    "INVALID_REDIRECT",
                                    "web redirect omitted its destination",
                                    retryable=False,
                                )
                            destination = urljoin(current_url, location)
                            if (
                                urlsplit(current_url).scheme == "https"
                                and urlsplit(destination).scheme == "http"
                            ):
                                raise WebFetchError(
                                    "REDIRECT_DOWNGRADE",
                                    "HTTPS web page redirected to HTTP",
                                    retryable=False,
                                )
                            current_url = destination
                            continue
                        return await self._read_response(
                            response,
                            requested_url=requested_url,
                            final_url=current_url,
                        )
        except WebFetchError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as error:
            if _has_unsafe_address_cause(error):
                raise WebFetchError(
                    "SSRF_BLOCKED",
                    "web host resolved to a non-public address",
                    retryable=False,
                ) from error
            raise WebFetchError(
                "NETWORK_ERROR",
                f"web page is unavailable: {type(error).__name__}",
                retryable=True,
            ) from error
        raise AssertionError("unreachable redirect state")

    async def _read_response(
        self,
        response: aiohttp.ClientResponse,
        *,
        requested_url: str,
        final_url: str,
    ) -> FetchedPage:
        if not 200 <= response.status < 300:
            retryable = response.status in {408, 409, 425, 429} or response.status >= 500
            raise WebFetchError(
                "HTTP_STATUS",
                f"web page returned HTTP {response.status}",
                retryable=retryable,
            )
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type not in _HTML_MEDIA_TYPES:
            raise WebFetchError(
                "UNSUPPORTED_CONTENT_TYPE",
                f"web page media type {media_type or 'missing'} is not HTML",
                retryable=False,
            )
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = -1
            if declared_length > self._max_response_bytes:
                raise WebFetchError(
                    "RESPONSE_TOO_LARGE",
                    "web page exceeded the response byte limit",
                    retryable=False,
                )
        chunks: list[bytes] = []
        consumed = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            consumed += len(chunk)
            if consumed > self._max_response_bytes:
                raise WebFetchError(
                    "RESPONSE_TOO_LARGE",
                    "web page exceeded the response byte limit",
                    retryable=False,
                )
            chunks.append(chunk)
        charset = response.charset or "utf-8"
        try:
            html = b"".join(chunks).decode(charset, errors="replace")
        except LookupError:
            html = b"".join(chunks).decode("utf-8", errors="replace")
        return FetchedPage(
            requested_url=requested_url,
            final_url=final_url,
            html=html,
            content_type=media_type,
            fetched_at=self._clock(),
            http_last_modified=_parse_http_date(response.headers.get("Last-Modified")),
        )

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[aiohttp.ClientSession]:
        if self._session is not None:
            yield self._session
            return
        resolver = PublicNetworkResolver()
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            ttl_dns_cache=60,
            limit_per_host=4,
        )
        async with aiohttp.ClientSession(
            connector=connector,
            trust_env=False,
            auto_decompress=True,
        ) as session:
            yield session


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_global
    return address.is_global


def _has_unsafe_address_cause(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, UnsafeAddressError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _parse_http_date(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        parsed = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
