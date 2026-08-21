from __future__ import annotations

import asyncio
import json
import re
import unittest

from fastapi.testclient import TestClient

from rag_api.app import create_app
from rag_api.config import Settings
from rag_api.core_client import CoreHealth, CoreUnavailableError
from rag_api.generation import AnswerPipelineError, AnswerUpdate


class FakeCoreClient:
    def __init__(self, *, ready: bool = True, unavailable: bool = False) -> None:
        self.ready = ready
        self.unavailable = unavailable

    async def health(self) -> CoreHealth:
        if self.unavailable:
            raise CoreUnavailableError("core unavailable in test")
        return CoreHealth(
            service="multimodal-rag-core",
            version="0.1.0",
            ready=self.ready,
        )

    async def execute_plan(self, plan):
        raise NotImplementedError

    async def close(self) -> None:
        pass


class FakeAnswerService:
    def __init__(
        self,
        *,
        error: AnswerPipelineError | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls = []

    async def stream(self, **arguments):
        self.calls.append(arguments)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        yield AnswerUpdate("planning", {"status": "completed", "routes": []})
        yield AnswerUpdate("sources", {"citations": [], "conflicts": []})
        yield AnswerUpdate("delta", {"text": "answer"})
        yield AnswerUpdate(
            "done", {"answer": "answer", "finish_reason": "completed"}
        )


class PythonApiTest(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            environment="test",
            api_prefix="/api/v1",
            _env_file=None,
        )
        self.answer_service = FakeAnswerService()
        self.client = TestClient(
            create_app(
                settings,
                FakeCoreClient(),
                answer_service=self.answer_service,
            )
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_liveness_echoes_safe_request_id(self) -> None:
        response = self.client.get(
            "/health/live", headers={"X-Request-ID": "review-request-42"}
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("review-request-42", response.headers["X-Request-ID"])
        self.assertEqual("review-request-42", response.json()["request_id"])
        self.assertTrue(response.json()["ready"])

    def test_readiness_reports_python_api_check(self) -> None:
        response = self.client.get("/health/ready")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"python_api": "ok", "rag_core": "ok"},
            response.json()["checks"],
        )
        self.assertEqual("test", response.json()["environment"])

    def test_readiness_returns_503_when_core_is_unavailable(self) -> None:
        settings = Settings(environment="test", _env_file=None)
        with TestClient(
            create_app(settings, FakeCoreClient(unavailable=True))
        ) as client:
            response = client.get(
                "/health/ready",
                headers={"X-Request-ID": "core-unavailable-request"},
            )

        self.assertEqual(503, response.status_code)
        self.assertFalse(response.json()["ready"])
        self.assertEqual("degraded", response.json()["status"])
        self.assertEqual(
            {"python_api": "ok", "rag_core": "unavailable"},
            response.json()["checks"],
        )
        self.assertEqual(
            "core-unavailable-request", response.headers["X-Request-ID"]
        )

    def test_malformed_request_id_is_replaced(self) -> None:
        response = self.client.get(
            "/health/live", headers={"X-Request-ID": "contains spaces"}
        )

        request_id = response.headers["X-Request-ID"]
        self.assertNotEqual("contains spaces", request_id)
        self.assertRegex(
            request_id,
            re.compile(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
            ),
        )

    def test_validation_errors_use_stable_envelope(self) -> None:
        response = self.client.post(
            "/api/v1/queries/stream",
            json={"query": "   "},
            headers={"X-Request-ID": "invalid-query-request"},
        )

        self.assertEqual(422, response.status_code)
        body = response.json()
        self.assertEqual("invalid-query-request", body["request_id"])
        self.assertEqual("VALIDATION_ERROR", body["error"]["code"])
        self.assertTrue(body["error"]["details"])

    def test_not_found_uses_stable_envelope(self) -> None:
        response = self.client.get(
            "/does-not-exist", headers={"X-Request-ID": "missing-route-request"}
        )

        self.assertEqual(404, response.status_code)
        self.assertEqual("missing-route-request", response.json()["request_id"])
        self.assertEqual("NOT_FOUND", response.json()["error"]["code"])

    def test_streaming_pipeline_emits_ordered_events_and_trusted_identity(self) -> None:
        with self.client.stream(
            "POST",
            "/api/v1/queries/stream",
            json={"query": "explain the architecture"},
            headers={
                "X-Request-ID": "stream-request-1",
                "X-Tenant-ID": "tenant-1",
                "X-User-ID": "user-1",
                "X-ACL-IDs": "team-a,private-user-1",
            },
        ) as response:
            content = "".join(response.iter_text())

        self.assertEqual(200, response.status_code)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertEqual("stream-request-1", response.headers["X-Request-ID"])
        self.assertLess(
            content.index("event: accepted"), content.index("event: done")
        )

        data_lines = [
            line.removeprefix("data: ")
            for line in content.splitlines()
            if line.startswith("data: ")
        ]
        events = [json.loads(line) for line in data_lines]
        self.assertEqual(
            ["accepted", "planning", "sources", "delta", "done"],
            [event["event"] for event in events],
        )
        self.assertEqual(list(range(5)), [event["sequence"] for event in events])
        call = self.answer_service.calls[0]
        self.assertEqual("tenant-1", call["tenant_id"])
        self.assertEqual("user-1", call["user_id"])
        self.assertEqual(("team-a", "private-user-1"), call["allowed_acl_ids"])

    def test_stream_requires_gateway_identity_and_rejects_body_user_id(self) -> None:
        missing_identity = self.client.post(
            "/api/v1/queries/stream", json={"query": "question"}
        )
        body_identity = self.client.post(
            "/api/v1/queries/stream",
            json={"query": "question", "user_id": "attacker"},
            headers={"X-Tenant-ID": "tenant-1", "X-User-ID": "user-1"},
        )

        self.assertEqual(422, missing_identity.status_code)
        self.assertEqual(422, body_identity.status_code)

    def test_pipeline_error_is_a_terminal_sanitized_sse_event(self) -> None:
        service = FakeAnswerService(
            error=AnswerPipelineError(
                "WEB_SEARCH_NOT_CONFIGURED", "联网搜索尚未配置"
            )
        )
        settings = Settings(environment="test", _env_file=None)
        with TestClient(
            create_app(settings, FakeCoreClient(), answer_service=service)
        ) as client:
            response = client.post(
                "/api/v1/queries/stream",
                json={"query": "latest", "retrieval_scope": "web"},
                headers={"X-Tenant-ID": "tenant-1", "X-User-ID": "user-1"},
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("event: error", response.text)
        self.assertIn("WEB_SEARCH_NOT_CONFIGURED", response.text)

    def test_heartbeat_does_not_restart_the_pending_pipeline_step(self) -> None:
        service = FakeAnswerService(delay_seconds=0.03)
        settings = Settings(
            environment="test",
            sse_heartbeat_seconds=0.01,
            _env_file=None,
        )
        with TestClient(
            create_app(settings, FakeCoreClient(), answer_service=service)
        ) as client:
            response = client.post(
                "/api/v1/queries/stream",
                json={"query": "slow question"},
                headers={"X-Tenant-ID": "tenant-1", "X-User-ID": "user-1"},
            )

        self.assertGreaterEqual(response.text.count("event: heartbeat"), 1)
        self.assertEqual(1, len(service.calls))
        self.assertIn("event: done", response.text)


if __name__ == "__main__":
    unittest.main()
