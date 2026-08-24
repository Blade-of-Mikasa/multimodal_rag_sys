from __future__ import annotations

import unittest

from rag_api.domain import Modality, SourceScope
from rag_api.generation import (
    AnswerPipelineError,
    AnswerPreferences,
    ChatCompletion,
    ModelQueryPlanner,
)


class FakeChatModel:
    model_id = "planner"
    model_version = "test"

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return ChatCompletion(self.text, "completed")


class QueryPlannerTest(unittest.IsolatedAsyncioTestCase):
    async def test_enforces_hybrid_scope_and_allowed_modalities(self) -> None:
        model = FakeChatModel(
            """{"routes":[
                {"query":"diagram","source_scope":"local","modality":"image"},
                {"query":"latest","source_scope":"web","modality":"document"},
                {"query":"clip","source_scope":"local","modality":"video"}
            ]}"""
        )
        planner = ModelQueryPlanner(model)

        plan = await planner.plan(
            "explain",
            AnswerPreferences(
                retrieval_scope="hybrid",
                modalities=(Modality.DOCUMENT, Modality.IMAGE),
            ),
        )

        self.assertEqual(2, len(plan.routes))
        self.assertEqual(
            {SourceScope.LOCAL, SourceScope.WEB},
            {route.source_scope for route in plan.routes},
        )
        self.assertTrue(model.requests[0].response_schema)
        self.assertEqual("planner", plan.model_id)
        self.assertEqual("test", plan.model_version)
        self.assertIsNotNone(plan.usage)

    async def test_local_scope_filters_web_and_deduplicates_routes(self) -> None:
        route = (
            '{"query":"same","source_scope":"local",'
            '"modality":"document"}'
        )
        planner = ModelQueryPlanner(
            FakeChatModel(
                f'{{"routes":[{route},{route},'
                '{"query":"web","source_scope":"web",'
                '"modality":"document"}]}'
            )
        )

        plan = await planner.plan(
            "question",
            AnswerPreferences(
                retrieval_scope="local", modalities=(Modality.DOCUMENT,)
            ),
        )

        self.assertEqual(1, len(plan.routes))
        self.assertIs(SourceScope.LOCAL, plan.routes[0].source_scope)

    async def test_rejects_impossible_web_modality_and_invalid_contract(self) -> None:
        valid = FakeChatModel(
            '{"routes":[{"query":"image","source_scope":"local",'
            '"modality":"image"}]}'
        )
        with self.assertRaises(AnswerPipelineError) as preferences:
            await ModelQueryPlanner(valid).plan(
                "image",
                AnswerPreferences(
                    retrieval_scope="web", modalities=(Modality.IMAGE,)
                ),
            )
        self.assertEqual("INVALID_PREFERENCES", preferences.exception.code)

        with self.assertRaises(AnswerPipelineError) as contract:
            await ModelQueryPlanner(FakeChatModel("not-json")).plan(
                "question", AnswerPreferences()
            )
        self.assertEqual("PLANNER_CONTRACT_INVALID", contract.exception.code)


if __name__ == "__main__":
    unittest.main()
