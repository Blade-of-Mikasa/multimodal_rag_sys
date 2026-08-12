"""Async Python adapter for the generated C++ Core gRPC contract."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from types import ModuleType
from typing import Protocol

import grpc

from .domain import ExecutionPlan


class CoreUnavailableError(RuntimeError):
    """Raised when the C++ Core contract or process cannot be reached."""


@dataclass(frozen=True)
class CoreHealth:
    service: str
    version: str
    ready: bool


@dataclass(frozen=True)
class CorePlanResult:
    request_id: str
    context: str
    evidence_count: int
    route_error_codes: tuple[str, ...]
    partial_failure: bool


class CoreClient(Protocol):
    async def health(self) -> CoreHealth: ...

    async def execute_plan(self, plan: ExecutionPlan) -> CorePlanResult: ...

    async def close(self) -> None: ...


class GrpcCoreClient:
    """Owns a lazy gRPC channel and maps generated messages to domain values."""

    def __init__(self, target: str, timeout_seconds: float = 1.0) -> None:
        self._target = target
        self._timeout_seconds = timeout_seconds
        self._channel: grpc.aio.Channel | None = None
        self._messages: ModuleType | None = None
        self._stub = None

    def _ensure_stub(self):
        if self._stub is not None:
            return self._stub
        try:
            self._messages = importlib.import_module("rag_core_pb2")
            services = importlib.import_module("rag_core_pb2_grpc")
        except ModuleNotFoundError as error:
            raise CoreUnavailableError(
                "generated Python gRPC contract is unavailable; "
                "run ./scripts/generate_proto.sh and include "
                "build/generated/python in PYTHONPATH"
            ) from error

        self._channel = grpc.aio.insecure_channel(self._target)
        self._stub = services.RagCoreServiceStub(self._channel)
        return self._stub

    async def health(self) -> CoreHealth:
        stub = self._ensure_stub()
        assert self._messages is not None
        try:
            response = await stub.Health(
                self._messages.HealthRequest(),
                timeout=self._timeout_seconds,
                wait_for_ready=True,
            )
        except grpc.aio.AioRpcError as error:
            raise CoreUnavailableError(
                f"C++ Core health check failed: {error.code().name}"
            ) from error
        return CoreHealth(
            service=response.service,
            version=response.version,
            ready=response.ready,
        )

    async def execute_plan(self, plan: ExecutionPlan) -> CorePlanResult:
        validation_errors = plan.validate()
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        stub = self._ensure_stub()
        assert self._messages is not None
        request = self._messages.ExecutePlanRequest(
            request_id=plan.request_id,
            user_id=plan.user_id,
            conversation_id=plan.conversation_id,
            allowed_acl_ids=plan.allowed_acl_ids,
            routes=[
                self._messages.RetrievalRoute(
                    route_id=route.route_id,
                    query=route.query,
                    source_scope=int(route.source_scope),
                    modality=int(route.modality),
                    top_k=route.top_k,
                    timeout_ms=route.timeout_ms,
                )
                for route in plan.routes
            ],
        )
        try:
            response = await stub.ExecutePlan(
                request,
                timeout=self._timeout_seconds,
                wait_for_ready=True,
            )
        except grpc.aio.AioRpcError as error:
            raise CoreUnavailableError(
                f"C++ Core ExecutePlan failed: {error.code().name}"
            ) from error
        return CorePlanResult(
            request_id=response.request_id,
            context=response.context,
            evidence_count=len(response.evidence),
            route_error_codes=tuple(error.code for error in response.route_errors),
            partial_failure=response.partial_failure,
        )

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None
