from __future__ import annotations

import unittest

from rag_api.domain import ExecutionPlan, Modality, RetrievalRoute, SourceScope


def valid_route(route_id: str = "route-1") -> RetrievalRoute:
    return RetrievalRoute(
        route_id=route_id,
        query="multimodal RAG architecture",
        source_scope=SourceScope.LOCAL,
        modality=Modality.DOCUMENT,
        top_k=20,
        timeout_ms=1_500,
        dense_embedding=(1.0, 0.0),
        embedding_model_id="embedding-general",
        embedding_model_version="v1",
    )


class ExecutionPlanTest(unittest.TestCase):
    def test_valid_plan(self) -> None:
        plan = ExecutionPlan(
            request_id="request-1",
            tenant_id="tenant-1",
            allowed_acl_ids=("public",),
            routes=(valid_route(),),
        )
        self.assertEqual([], plan.validate())

    def test_invalid_route(self) -> None:
        route = RetrievalRoute(
            route_id="route-1",
            query="",
            source_scope=SourceScope.LOCAL,
            modality=Modality.DOCUMENT,
            top_k=0,
            timeout_ms=50,
            dense_embedding=(1.0, 0.0),
            embedding_model_id="embedding-general",
            embedding_model_version="v1",
        )
        self.assertEqual(3, len(route.validate()))

    def test_duplicate_route_id(self) -> None:
        plan = ExecutionPlan(
            request_id="request-2",
            tenant_id="tenant-1",
            allowed_acl_ids=("public",),
            routes=(valid_route(), valid_route()),
        )
        self.assertEqual(["route_id must be unique"], plan.validate())

    def test_route_count_is_capped_at_six(self) -> None:
        plan = ExecutionPlan(
            request_id="request-3",
            tenant_id="tenant-1",
            allowed_acl_ids=("public",),
            routes=tuple(valid_route(f"route-{index}") for index in range(7)),
        )
        self.assertIn("route count must not exceed 6", plan.validate())


if __name__ == "__main__":
    unittest.main()
