from __future__ import annotations

import importlib
import unittest

from rag_api.core_client import GrpcCoreClient
from rag_api.domain import ExecutionPlan, ExternalEvidence, Modality, SourceScope


class _FakePlanStub:
    def __init__(self, response: object) -> None:
        self.response = response
        self.request = None

    async def ExecutePlan(
        self, request: object, *, timeout: float, wait_for_ready: bool
    ) -> object:
        self.request = request
        self.timeout = timeout
        self.wait_for_ready = wait_for_ready
        return self.response


class CoreClientEvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_maps_external_request_and_auditable_response(self) -> None:
        try:
            messages = importlib.import_module("rag_core_pb2")
        except ModuleNotFoundError:
            self.skipTest("requires generated Protobuf modules")

        response = messages.ExecutePlanResponse(
            request_id="request-web",
            context="bounded context",
            evidence=(
                messages.Evidence(
                    evidence_id="web-1",
                    content="source",
                    modality=messages.MODALITY_DOCUMENT,
                    source_scope=messages.SOURCE_SCOPE_WEB,
                ),
            ),
            citations=(
                messages.Citation(
                    citation_id=1,
                    evidence_id="web-1",
                    source="example.com",
                    url="https://example.com/source",
                    title="Source",
                    modality=messages.MODALITY_DOCUMENT,
                    metadata={"rank": "1"},
                ),
            ),
            conflicts=(
                messages.Conflict(
                    evidence_ids=("web-1", "web-2"),
                    type="direct_conflict",
                    reason="claim values differ",
                ),
            ),
            evidence_decisions=(
                messages.EvidenceDecision(
                    evidence_id="web-1",
                    disposition="selected",
                    representative_evidence_id="web-1",
                    reason="selected within context budget",
                ),
            ),
            context_token_count=15,
            context_truncated=True,
            token_count_method="utf8_byte_upper_bound",
        )
        stub = _FakePlanStub(response)
        client = GrpcCoreClient("unused:0")
        client._messages = messages
        client._stub = stub
        plan = ExecutionPlan(
            request_id="request-web",
            tenant_id="tenant-1",
            external_evidence=(
                ExternalEvidence(
                    evidence_id="web-1",
                    content="source",
                    modality=Modality.DOCUMENT,
                    source_scope=SourceScope.WEB,
                    source="example.com",
                    url="https://example.com/source",
                    retrieved_at_unix_ms=1_787_227_200_000,
                    score=1.0,
                    metadata=(("rank", "1"),),
                    content_sha256="a" * 64,
                ),
            ),
            context_token_budget=4_096,
            max_evidence_tokens=1_024,
        )

        result = await client.execute_plan(plan)

        self.assertIsNotNone(stub.request)
        self.assertEqual("web-1", stub.request.external_evidence[0].evidence_id)
        self.assertEqual(4_096, stub.request.context_token_budget)
        self.assertEqual(1_024, stub.request.max_evidence_tokens)
        self.assertEqual(1, result.evidence_count)
        self.assertEqual(1, result.citations[0].citation_id)
        self.assertEqual((("rank", "1"),), result.citations[0].metadata)
        self.assertEqual("direct_conflict", result.conflicts[0].type)
        self.assertEqual("selected", result.evidence_decisions[0].disposition)
        self.assertEqual(15, result.context_token_count)
        self.assertTrue(result.context_truncated)
        self.assertEqual("utf8_byte_upper_bound", result.token_count_method)


if __name__ == "__main__":
    unittest.main()
