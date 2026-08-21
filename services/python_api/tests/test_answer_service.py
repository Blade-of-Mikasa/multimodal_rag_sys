from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import unittest

from rag_api.core_client import CoreCitation, CorePlanResult
from rag_api.domain import Modality, SourceScope
from rag_api.generation import (
    AnswerPipelineError,
    AnswerPreferences,
    AnswerService,
    ChatDelta,
    PlannedRoute,
    RetrievalPlan,
)
from rag_api.web.domain import ExtractionStatus, WebSearchBundle, WebSource


class FakePlanner:
    async def plan(self, query, preferences):
        return RetrievalPlan(
            (
                PlannedRoute(query, SourceScope.LOCAL, Modality.DOCUMENT),
                PlannedRoute(query, SourceScope.WEB, Modality.DOCUMENT),
            )
        )


class FakeEmbeddingModel:
    model_id = "embedding-general"
    model_version = "2026-08"
    dimension = 2

    async def embed(self, texts):
        return tuple((1.0, 0.0) for _ in texts)


class FakeWebSearcher:
    async def search(self, query):
        observed_at = datetime(2026, 8, 21, tzinfo=timezone.utc)
        text = "web source"
        return WebSearchBundle(
            provider="bing",
            query=query.text,
            search_urls=(),
            grounded_text="excluded",
            sources=(
                WebSource(
                    rank=1,
                    url="https://example.com/source",
                    title="Web source",
                    text=text,
                    status=ExtractionStatus.FULL,
                    fetched_at=observed_at,
                    content_sha256=sha256(text.encode()).hexdigest(),
                ),
            ),
        )


class FailingWebSearcher:
    async def search(self, query):
        raise RuntimeError("provider detail must not escape")


class EmptyWebSearcher:
    async def search(self, query):
        return WebSearchBundle(
            provider="bing",
            query=query.text,
            search_urls=(),
            grounded_text="",
            sources=(),
        )


class WebOnlyPlanner:
    async def plan(self, query, preferences):
        return RetrievalPlan(
            (PlannedRoute(query, SourceScope.WEB, Modality.DOCUMENT),)
        )


class FakeCoreClient:
    def __init__(self, *, evidence_count: int = 1) -> None:
        self.evidence_count = evidence_count
        self.plan = None

    async def execute_plan(self, plan):
        self.plan = plan
        citations = (
            CoreCitation(
                citation_id=1,
                evidence_id="local-1",
                source="manual.pdf",
                url="",
                title="Manual",
                modality=Modality.DOCUMENT,
                metadata=(),
            ),
        ) if self.evidence_count else ()
        return CorePlanResult(
            request_id=plan.request_id,
            context='{"citation_id":1,"content_untrusted_json":"fact"}',
            evidence_count=self.evidence_count,
            citations=citations,
            conflicts=(),
            evidence_decisions=(),
            context_token_count=12,
            context_truncated=False,
            token_count_method="utf8_byte_upper_bound",
            route_error_codes=(),
            partial_failure=False,
        )


class FakeChatModel:
    model_id = "chat-general"
    model_version = "2026-08"

    async def stream(self, request):
        yield ChatDelta("事实 [证据 1]")
        yield ChatDelta(finish_reason="completed")


def service(core, web_searcher=None, planner=None):
    return AnswerService(
        planner=planner or FakePlanner(),
        embedding_model=FakeEmbeddingModel(),
        core_client=core,
        chat_model=FakeChatModel(),
        web_searcher=web_searcher,
    )


async def collect(
    answer_service,
    preferences=AnswerPreferences(retrieval_scope="hybrid"),
):
    return [
        update
        async for update in answer_service.stream(
            request_id="request-1",
            query="question",
            tenant_id="tenant-1",
            user_id="user-1",
            conversation_id="conversation-1",
            allowed_acl_ids=("team-a",),
            preferences=preferences,
        )
    ]


class AnswerServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_builds_local_and_web_plan_then_audits_citations(self) -> None:
        core = FakeCoreClient()

        updates = await collect(service(core, FakeWebSearcher()))

        self.assertEqual(
            [
                "planning",
                "planning",
                "retrieving",
                "retrieving",
                "sources",
                "delta",
                "done",
            ],
            [update.event for update in updates],
        )
        self.assertEqual(("team-a",), core.plan.allowed_acl_ids)
        self.assertEqual(1, len(core.plan.routes))
        self.assertEqual(1, len(core.plan.external_evidence))
        self.assertIn(
            ("route_id", "web:1"), core.plan.external_evidence[0].metadata
        )
        self.assertEqual([1], updates[-1].data["referenced_citation_ids"])
        self.assertFalse(updates[-1].data["uncited_answer"])

    async def test_hybrid_web_failure_degrades_to_local_evidence(self) -> None:
        updates = await collect(service(FakeCoreClient(), FailingWebSearcher()))

        sources = next(update for update in updates if update.event == "sources")
        self.assertTrue(sources.data["partial_failure"])
        self.assertEqual(
            ["WEB_SEARCH_ROUTE_FAILED"], sources.data["route_error_codes"]
        )

    async def test_zero_evidence_does_not_call_generation(self) -> None:
        updates = await collect(
            service(FakeCoreClient(evidence_count=0), FakeWebSearcher())
        )

        self.assertEqual("insufficient_evidence", updates[-1].data["finish_reason"])
        self.assertIn("未找到", updates[-1].data["answer"])

    async def test_auto_scope_degrades_when_web_is_not_configured(self) -> None:
        updates = await collect(
            service(FakeCoreClient()),
            AnswerPreferences(retrieval_scope="auto"),
        )

        sources = next(update for update in updates if update.event == "sources")
        self.assertTrue(sources.data["partial_failure"])
        self.assertIn(
            "WEB_SEARCH_NOT_CONFIGURED", sources.data["route_error_codes"]
        )

    async def test_valid_empty_web_result_returns_insufficient_evidence(self) -> None:
        updates = await collect(
            service(
                FakeCoreClient(),
                EmptyWebSearcher(),
                planner=WebOnlyPlanner(),
            ),
            AnswerPreferences(retrieval_scope="web"),
        )

        self.assertEqual("insufficient_evidence", updates[-1].data["finish_reason"])
        self.assertFalse(
            next(update for update in updates if update.event == "sources").data[
                "partial_failure"
            ]
        )

    async def test_all_web_routes_failing_is_a_retryable_error(self) -> None:
        with self.assertRaises(AnswerPipelineError) as caught:
            await collect(
                service(
                    FakeCoreClient(),
                    FailingWebSearcher(),
                    planner=WebOnlyPlanner(),
                ),
                AnswerPreferences(retrieval_scope="web"),
            )

        self.assertEqual("WEB_SEARCH_FAILED", caught.exception.code)
        self.assertTrue(caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
