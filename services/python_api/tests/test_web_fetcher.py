from __future__ import annotations

from datetime import datetime, timezone
import socket
import unittest

from aiohttp.abc import ResolveResult

from rag_api.web.domain import WebFetchError
from rag_api.web.fetcher import (
    PublicNetworkResolver,
    PublicUrlPolicy,
    SafeWebFetcher,
    UnsafeAddressError,
)


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        charset: str | None = "utf-8",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(chunks or [])
        self.charset = charset

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, object]] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


class FakeResolver:
    def __init__(self, address: str) -> None:
        self.address = address

    async def resolve(self, host: str, port: int, family: socket.AddressFamily):
        return [
            ResolveResult(
                hostname=host,
                host=self.address,
                port=port,
                family=family,
                proto=socket.IPPROTO_TCP,
                flags=0,
            )
        ]

    async def close(self) -> None:
        pass


class PublicUrlPolicyTest(unittest.TestCase):
    def test_rejects_private_credentials_local_names_and_non_web_ports(self) -> None:
        policy = PublicUrlPolicy()
        blocked = (
            "http://127.0.0.1/admin",
            "http://169.254.169.254/latest/meta-data",
            "http://100.64.0.1/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "https://user:secret@example.com/",
            "https://service.internal/",
            "https://example.com:8443/",
        )
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(WebFetchError):
                policy.validate(url)
        self.assertEqual(
            "https://example.com/path", policy.validate("https://example.com/path#part")
        )


class PublicNetworkResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_private_dns_answers_and_accepts_public_answers(self) -> None:
        with self.assertRaises(UnsafeAddressError):
            await PublicNetworkResolver(FakeResolver("10.0.0.8")).resolve(
                "example.com", 443
            )
        answers = await PublicNetworkResolver(FakeResolver("93.184.216.34")).resolve(
            "example.com", 443
        )
        self.assertEqual("93.184.216.34", answers[0]["host"])


class SafeWebFetcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_validates_each_redirect_and_reads_bounded_html(self) -> None:
        session = FakeSession(
            [
                FakeResponse(302, headers={"Location": "/article"}),
                FakeResponse(
                    200,
                    headers={
                        "Content-Type": "text/html; charset=utf-8",
                        "Last-Modified": "Thu, 20 Aug 2026 08:00:00 GMT",
                    },
                    chunks=[b"<article>", "正文".encode(), b"</article>"],
                ),
            ]
        )
        now = datetime(2026, 8, 20, 9, tzinfo=timezone.utc)
        fetcher = SafeWebFetcher(
            timeout_seconds=2,
            max_response_bytes=1024,
            max_redirects=2,
            user_agent="rag-test/1",
            clock=lambda: now,
            session=session,
        )

        page = await fetcher.fetch("https://example.com/start#fragment")

        self.assertEqual("https://example.com/start", page.requested_url)
        self.assertEqual("https://example.com/article", page.final_url)
        self.assertIn("正文", page.html)
        self.assertEqual(now, page.fetched_at)
        self.assertEqual(
            datetime(2026, 8, 20, 8, tzinfo=timezone.utc),
            page.http_last_modified,
        )
        self.assertEqual(2, len(session.calls))
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_rejects_oversized_and_non_html_responses(self) -> None:
        oversized = SafeWebFetcher(
            timeout_seconds=2,
            max_response_bytes=5,
            max_redirects=0,
            user_agent="rag-test/1",
            session=FakeSession(
                [
                    FakeResponse(
                        200,
                        headers={"Content-Type": "text/html"},
                        chunks=[b"123", b"456"],
                    )
                ]
            ),
        )
        with self.assertRaises(WebFetchError) as raised:
            await oversized.fetch("https://example.com")
        self.assertEqual("RESPONSE_TOO_LARGE", raised.exception.code)

        non_html = SafeWebFetcher(
            timeout_seconds=2,
            max_response_bytes=1024,
            max_redirects=0,
            user_agent="rag-test/1",
            session=FakeSession(
                [FakeResponse(200, headers={"Content-Type": "application/pdf"})]
            ),
        )
        with self.assertRaises(WebFetchError) as raised:
            await non_html.fetch("https://example.com/file.pdf")
        self.assertEqual("UNSUPPORTED_CONTENT_TYPE", raised.exception.code)

    async def test_blocks_a_redirect_before_requesting_its_private_target(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    headers={"Location": "https://169.254.169.254/latest/meta-data"},
                )
            ]
        )
        fetcher = SafeWebFetcher(
            timeout_seconds=2,
            max_response_bytes=1024,
            max_redirects=2,
            user_agent="rag-test/1",
            session=session,
        )

        with self.assertRaises(WebFetchError) as raised:
            await fetcher.fetch("https://example.com/start")

        self.assertEqual("SSRF_BLOCKED", raised.exception.code)
        self.assertEqual(1, len(session.calls))


if __name__ == "__main__":
    unittest.main()
