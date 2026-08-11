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
    )
    decoded = messages.ExecutePlanRequest.FromString(request.SerializeToString())
    if decoded.request_id != "req-m01-contract" or decoded.routes[0].top_k != 8:
        raise AssertionError("generated message round trip changed values")

    for stub_name in ("RagCoreServiceStub", "IndexCoreServiceStub"):
        if not hasattr(services, stub_name):
            raise AssertionError(f"missing generated stub: {stub_name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_generated_contract.py GENERATED_DIR")
    verify(pathlib.Path(sys.argv[1]).resolve())
    print("python_generated_contract_test: PASS")
