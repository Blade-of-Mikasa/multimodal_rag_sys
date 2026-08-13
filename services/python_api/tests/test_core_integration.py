from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from rag_api.app import create_app
from rag_api.config import Settings
from rag_api.core_client import GrpcCoreClient, IndexAssetCommand, IndexUnit
from rag_api.domain import ExecutionPlan, Modality, RetrievalRoute, SourceScope


CORE_TARGET = os.environ.get("RAG_CORE_TEST_TARGET")


@unittest.skipUnless(CORE_TARGET, "requires a running M03 C++ Core")
class CoreClientIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert CORE_TARGET is not None
        self.client = GrpcCoreClient(
            CORE_TARGET,
            timeout_seconds=2.0,
            index_batch_max_bytes=65_536,
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_health_crosses_the_python_cpp_boundary(self) -> None:
        health = await self.client.health()

        self.assertEqual("multimodal-rag-core", health.service)
        self.assertEqual("0.1.0", health.version)
        self.assertTrue(health.ready)

    async def test_execute_plan_echoes_the_request_id(self) -> None:
        await self.client.index_asset(
            IndexAssetCommand(
                request_id="req-m07-index",
                tenant_id="tenant-m07",
                acl_id="acl-m07",
                asset_id="asset-m07",
                asset_version_id="version-m07",
                asset_version=1,
                object_key="tenant-m07/asset-m07/v1/document.txt",
                units=(
                    IndexUnit(
                        unit_id="chunk-m07",
                        modality=Modality.DOCUMENT,
                        content="Python and C++ Milvus architecture " * 1_200,
                        title="Architecture",
                        ordinal=0,
                        page_number=0,
                        content_sha256="a" * 64,
                        dense_embedding=(1.0, 0.0),
                        embedding_model_id="embedding-general",
                        embedding_model_version="v1",
                    ),
                    IndexUnit(
                        unit_id="chunk-m07-append",
                        modality=Modality.DOCUMENT,
                        content="Bounded gRPC batches append safely " * 1_200,
                        title="Batching",
                        ordinal=1,
                        page_number=1,
                        content_sha256="b" * 64,
                        dense_embedding=(0.8, 0.2),
                        embedding_model_id="embedding-general",
                        embedding_model_version="v1",
                    ),
                ),
            )
        )
        plan = ExecutionPlan(
            request_id="req-m03-integration",
            user_id="user-m03",
            tenant_id="tenant-m07",
            allowed_acl_ids=("acl-m07",),
            routes=(
                RetrievalRoute(
                    route_id="route-local-doc",
                    query="Python to C++ contract test",
                    source_scope=SourceScope.LOCAL,
                    modality=Modality.DOCUMENT,
                    top_k=8,
                    timeout_ms=1_000,
                    dense_embedding=(1.0, 0.0),
                    embedding_model_id="embedding-general",
                    embedding_model_version="v1",
                ),
            ),
        )

        result = await self.client.execute_plan(plan)

        self.assertEqual("req-m03-integration", result.request_id)
        self.assertEqual("", result.context)
        self.assertEqual(2, result.evidence_count)
        self.assertEqual((), result.route_error_codes)
        self.assertFalse(result.partial_failure)

    async def test_image_index_and_retrieval_cross_the_cpp_boundary(self) -> None:
        result = await self.client.index_asset(
            IndexAssetCommand(
                request_id="req-m08-image-index",
                tenant_id="tenant-m08",
                acl_id="acl-m08",
                asset_id="asset-image-m08",
                asset_version_id="version-image-m08",
                asset_version=1,
                object_key="tenant-m08/asset-image-m08/v1/image.png",
                units=(
                    IndexUnit(
                        unit_id="image-m08",
                        modality=Modality.IMAGE,
                        content="A red bicycle outside a cafe\nOCR:\nOPEN",
                        title="A red bicycle outside a cafe",
                        ordinal=0,
                        page_number=0,
                        content_sha256="c" * 64,
                        dense_embedding=(1.0, 0.0),
                        embedding_model_id="embedding-general",
                        embedding_model_version="v1",
                        metadata=(
                            ("media_type", "image/png"),
                            ("width", "800"),
                            ("height", "600"),
                            ("ocr_text", "OPEN"),
                            ("vision_model_id", "vision-general"),
                            ("vision_model_version", "v1"),
                        ),
                    ),
                ),
            )
        )
        self.assertTrue(result.collection_alias.startswith("rag_image_v1_"))

        plan = ExecutionPlan(
            request_id="req-m08-image-query",
            tenant_id="tenant-m08",
            allowed_acl_ids=("acl-m08",),
            routes=(
                RetrievalRoute(
                    route_id="route-local-image",
                    query="red bicycle",
                    source_scope=SourceScope.LOCAL,
                    modality=Modality.IMAGE,
                    top_k=5,
                    timeout_ms=1_000,
                    dense_embedding=(1.0, 0.0),
                    embedding_model_id="embedding-general",
                    embedding_model_version="v1",
                ),
            ),
        )
        query_result = await self.client.execute_plan(plan)

        self.assertEqual(1, query_result.evidence_count)
        self.assertEqual((), query_result.route_error_codes)
        self.assertFalse(query_result.partial_failure)


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
