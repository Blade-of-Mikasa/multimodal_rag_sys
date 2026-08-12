from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from rag_api.app import create_app
from rag_api.config import Settings
from rag_api.core_client import GrpcCoreClient
from rag_api.domain import ExecutionPlan, Modality, RetrievalRoute, SourceScope


CORE_TARGET = os.environ.get("RAG_CORE_TEST_TARGET")


@unittest.skipUnless(CORE_TARGET, "requires a running M03 C++ Core")
class CoreClientIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert CORE_TARGET is not None
        self.client = GrpcCoreClient(CORE_TARGET, timeout_seconds=2.0)

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_health_crosses_the_python_cpp_boundary(self) -> None:
        health = await self.client.health()

        self.assertEqual("multimodal-rag-core", health.service)
        self.assertEqual("0.1.0", health.version)
        self.assertTrue(health.ready)

    async def test_execute_plan_echoes_the_request_id(self) -> None:
        plan = ExecutionPlan(
            request_id="req-m03-integration",
            user_id="user-m03",
            routes=(
                RetrievalRoute(
                    route_id="route-local-doc",
                    query="Python to C++ contract test",
                    source_scope=SourceScope.LOCAL,
                    modality=Modality.DOCUMENT,
                    top_k=8,
                    timeout_ms=1_000,
                ),
            ),
        )

        result = await self.client.execute_plan(plan)

        self.assertEqual("req-m03-integration", result.request_id)
        self.assertEqual("", result.context)
        self.assertEqual(0, result.evidence_count)
        self.assertEqual((), result.route_error_codes)
        self.assertFalse(result.partial_failure)


@unittest.skipUnless(CORE_TARGET, "requires a running M03 C++ Core")
class CoreReadinessIntegrationTest(unittest.TestCase):
    def test_api_readiness_checks_the_real_core(self) -> None:
        assert CORE_TARGET is not None
        settings = Settings(
            environment="test",
            core_grpc_target=CORE_TARGET,
            core_grpc_timeout_seconds=2.0,
            _env_file=None,
        )
        with TestClient(create_app(settings)) as client:
            response = client.get(
                "/health/ready",
                headers={"X-Request-ID": "req-m03-readiness"},
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["ready"])
        self.assertEqual("ok", response.json()["checks"]["rag_core"])
        self.assertEqual("req-m03-readiness", response.json()["request_id"])


if __name__ == "__main__":
    unittest.main()
