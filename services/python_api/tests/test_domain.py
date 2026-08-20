from __future__ import annotations

import unittest

from rag_api.domain import (
    ExecutionPlan,
    ExternalEvidence,
    Modality,
    RetrievalRoute,
    SourceScope,
)


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

        video_route = RetrievalRoute(
            route_id="video-local",
            query="jump to the retrieval explanation",
            source_scope=SourceScope.LOCAL,
            modality=Modality.VIDEO,
            dense_embedding=(1.0, 0.0),
            embedding_model_id="embedding-general",
            embedding_model_version="v1",
        )
        self.assertEqual([], video_route.validate())

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

    def test_web_only_plan_does_not_require_local_acl_scope(self) -> None:
        evidence = ExternalEvidence(
            evidence_id="web-1",
            content="Primary source text",
            modality=Modality.DOCUMENT,
            source_scope=SourceScope.WEB,
            url="https://example.com/source",
            score=1.0,
            content_sha256="a" * 64,
        )
        plan = ExecutionPlan(
            request_id="request-web",
            tenant_id="tenant-1",
            external_evidence=(evidence,),
        )

        self.assertEqual([], plan.validate())

    def test_external_evidence_and_context_budgets_are_validated(self) -> None:
        invalid = ExternalEvidence(
            evidence_id="web-1",
            content="source",
            modality=Modality.DOCUMENT,
            source_scope=SourceScope.LOCAL,
            url="file:///private/source",
        )
        plan = ExecutionPlan(
            request_id="request-invalid-web",
            tenant_id="tenant-1",
            external_evidence=(invalid,),
            context_token_budget=511,
            max_evidence_tokens=512,
        )

        errors = plan.validate()
        self.assertIn("external evidence must use WEB source_scope", errors)
        self.assertIn("web evidence must contain an HTTP(S) URL", errors)
        self.assertIn(
            "context_token_budget must be between 512 and 1000000", errors
        )

    def test_web_route_must_be_resolved_to_external_evidence(self) -> None:
        route = RetrievalRoute(
            route_id="web-route",
            query="current release",
            source_scope=SourceScope.WEB,
            modality=Modality.DOCUMENT,
        )

        self.assertIn(
            "Core retrieval routes must use LOCAL; pass web sources as "
            "external evidence",
            route.validate(),
        )


if __name__ == "__main__":
    unittest.main()
