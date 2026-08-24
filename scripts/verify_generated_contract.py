from __future__ import annotations

import importlib
import pathlib
import sys


def verify(generated_dir: pathlib.Path) -> None:
    if not generated_dir.is_dir():
        raise RuntimeError(f"generated directory does not exist: {generated_dir}")

    sys.path.insert(0, str(generated_dir))
    messages = importlib.import_module("rag_core_pb2")
    services = importlib.import_module("rag_core_pb2_grpc")

    if messages.DESCRIPTOR.package != "multimodal.rag.v1":
        raise AssertionError(f"unexpected package: {messages.DESCRIPTOR.package}")

    expected_services = {"RagCoreService", "IndexCoreService"}
    actual_services = set(messages.DESCRIPTOR.services_by_name)
    if actual_services != expected_services:
        raise AssertionError(f"unexpected services: {sorted(actual_services)}")

    request = messages.ExecutePlanRequest(
        request_id="req-m01-contract",
        user_id="user-m01",
        routes=[
            messages.RetrievalRoute(
                route_id="route-local-doc",
                query="contract smoke test",
                source_scope=messages.SOURCE_SCOPE_LOCAL,
                modality=messages.MODALITY_DOCUMENT,
                top_k=8,
            )
        ],
        external_evidence=[
            messages.Evidence(
                evidence_id="web-contract",
                content="public source",
                source_scope=messages.SOURCE_SCOPE_WEB,
                modality=messages.MODALITY_DOCUMENT,
                url="https://example.com/source",
                content_sha256="a" * 64,
            )
        ],
        context_token_budget=4096,
        max_evidence_tokens=1024,
    )
    decoded = messages.ExecutePlanRequest.FromString(request.SerializeToString())
    if (
        decoded.request_id != "req-m01-contract"
        or decoded.routes[0].top_k != 8
        or decoded.external_evidence[0].evidence_id != "web-contract"
        or decoded.context_token_budget != 4096
        or decoded.max_evidence_tokens != 1024
    ):
        raise AssertionError("generated message round trip changed values")

    response = messages.ExecutePlanResponse(
        request_id="req-m01-contract",
        context_token_count=512,
        context_truncated=True,
        token_count_method="utf8_byte_upper_bound",
        evidence_decisions=[
            messages.EvidenceDecision(
                evidence_id="web-contract",
                disposition="selected",
                representative_evidence_id="web-contract",
            )
        ],
    )
    decoded_response = messages.ExecutePlanResponse.FromString(
        response.SerializeToString()
    )
    if (
        decoded_response.context_token_count != 512
        or not decoded_response.context_truncated
        or decoded_response.evidence_decisions[0].disposition != "selected"
    ):
        raise AssertionError("generated response round trip changed values")

    for stub_name in ("RagCoreServiceStub", "IndexCoreServiceStub"):
        if not hasattr(services, stub_name):
            raise AssertionError(f"missing generated stub: {stub_name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_generated_contract.py GENERATED_DIR")
    verify(pathlib.Path(sys.argv[1]).resolve())
    print("python_generated_contract_test: PASS")
