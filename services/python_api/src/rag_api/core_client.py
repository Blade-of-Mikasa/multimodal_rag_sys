"""Async Python adapter for the generated C++ Core gRPC contract."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from math import isfinite
from types import ModuleType
from typing import Protocol

import grpc

from .domain import ExecutionPlan, Modality


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
    citations: tuple[CoreCitation, ...]
    conflicts: tuple[CoreConflict, ...]
    evidence_decisions: tuple[CoreEvidenceDecision, ...]
    context_token_count: int
    context_truncated: bool
    token_count_method: str
    route_error_codes: tuple[str, ...]
    partial_failure: bool


@dataclass(frozen=True, slots=True)
class CoreCitation:
    citation_id: int
    evidence_id: str
    source: str
    url: str
    title: str
    modality: Modality
    metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CoreConflict:
    evidence_ids: tuple[str, ...]
    type: str
    reason: str


@dataclass(frozen=True, slots=True)
class CoreEvidenceDecision:
    evidence_id: str
    disposition: str
    representative_evidence_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class IndexUnit:
    unit_id: str
    modality: Modality
    content: str
    title: str
    ordinal: int
    page_number: int
    content_sha256: str
    dense_embedding: tuple[float, ...]
    embedding_model_id: str
    embedding_model_version: str
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class IndexAssetCommand:
    request_id: str
    tenant_id: str
    acl_id: str
    asset_id: str
    asset_version_id: str
    asset_version: int
    object_key: str
    units: tuple[IndexUnit, ...]


@dataclass(frozen=True, slots=True)
class IndexAssetResult:
    request_id: str
    asset_id: str
    asset_version: int
    indexed_unit_count: int
    collection_alias: str


class CoreClient(Protocol):
    async def health(self) -> CoreHealth: ...

    async def execute_plan(self, plan: ExecutionPlan) -> CorePlanResult: ...

    async def index_asset(self, command: IndexAssetCommand) -> IndexAssetResult: ...

    async def close(self) -> None: ...


class GrpcCoreClient:
    """Owns a lazy gRPC channel and maps generated messages to domain values."""

    def __init__(
        self,
        target: str,
        timeout_seconds: float = 1.0,
        index_timeout_seconds: float = 60.0,
        index_batch_max_bytes: int = 3_000_000,
    ) -> None:
        if not 65_536 <= index_batch_max_bytes <= 3_500_000:
            raise ValueError(
                "index_batch_max_bytes must be between 65536 and 3500000"
            )
        self._target = target
        self._timeout_seconds = timeout_seconds
        self._index_timeout_seconds = index_timeout_seconds
        self._index_batch_max_bytes = index_batch_max_bytes
        self._channel: grpc.aio.Channel | None = None
        self._messages: ModuleType | None = None
        self._stub = None
        self._index_stub = None

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
        self._index_stub = services.IndexCoreServiceStub(self._channel)
        return self._stub

    def _ensure_index_stub(self):
        self._ensure_stub()
        return self._index_stub

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
            tenant_id=plan.tenant_id,
            allowed_acl_ids=plan.allowed_acl_ids,
            routes=[
                self._messages.RetrievalRoute(
                    route_id=route.route_id,
                    query=route.query,
                    source_scope=int(route.source_scope),
                    modality=int(route.modality),
                    top_k=route.top_k,
                    timeout_ms=route.timeout_ms,
                    dense_embedding=route.dense_embedding,
                    embedding_model_id=route.embedding_model_id,
                    embedding_model_version=route.embedding_model_version,
                )
                for route in plan.routes
            ],
            external_evidence=[
                self._messages.Evidence(
                    evidence_id=evidence.evidence_id,
                    content=evidence.content,
                    modality=int(evidence.modality),
                    source_scope=int(evidence.source_scope),
                    title=evidence.title,
                    source=evidence.source,
                    url=evidence.url,
                    published_at_unix_ms=evidence.published_at_unix_ms,
                    retrieved_at_unix_ms=evidence.retrieved_at_unix_ms,
                    score=evidence.score,
                    metadata=dict(evidence.metadata),
                    content_sha256=evidence.content_sha256,
                )
                for evidence in plan.external_evidence
            ],
            context_token_budget=plan.context_token_budget,
            max_evidence_tokens=plan.max_evidence_tokens,
        )
        try:
            response = await stub.ExecutePlan(
                request,
                timeout=self._timeout_seconds,
                wait_for_ready=True,
            )
        except grpc.aio.AioRpcError as error:
            if error.code() is grpc.StatusCode.INVALID_ARGUMENT:
                raise ValueError(
                    f"C++ Core rejected ExecutePlan: {error.details()}"
                ) from error
            raise CoreUnavailableError(
                f"C++ Core ExecutePlan failed: {error.code().name}"
            ) from error
        return CorePlanResult(
            request_id=response.request_id,
            context=response.context,
            evidence_count=len(response.evidence),
            citations=tuple(
                CoreCitation(
                    citation_id=citation.citation_id,
                    evidence_id=citation.evidence_id,
                    source=citation.source,
                    url=citation.url,
                    title=citation.title,
                    modality=Modality(citation.modality),
                    metadata=tuple(sorted(citation.metadata.items())),
                )
                for citation in response.citations
            ),
            conflicts=tuple(
                CoreConflict(
                    evidence_ids=tuple(conflict.evidence_ids),
                    type=conflict.type,
                    reason=conflict.reason,
                )
                for conflict in response.conflicts
            ),
            evidence_decisions=tuple(
                CoreEvidenceDecision(
                    evidence_id=decision.evidence_id,
                    disposition=decision.disposition,
                    representative_evidence_id=(
                        decision.representative_evidence_id
                    ),
                    reason=decision.reason,
                )
                for decision in response.evidence_decisions
            ),
            context_token_count=response.context_token_count,
            context_truncated=response.context_truncated,
            token_count_method=response.token_count_method,
            route_error_codes=tuple(error.code for error in response.route_errors),
            partial_failure=response.partial_failure,
        )

    async def index_asset(
        self, command: IndexAssetCommand
    ) -> IndexAssetResult:
        self._validate_index_command(command)
        stub = self._ensure_index_stub()
        assert self._messages is not None
        unit_messages = tuple(
            self._messages.NormalizedUnit(
                unit_id=unit.unit_id,
                modality=int(unit.modality),
                content=unit.content,
                title=unit.title,
                ordinal=unit.ordinal,
                page_number=unit.page_number,
                content_sha256=unit.content_sha256,
                dense_embedding=unit.dense_embedding,
                embedding_model_id=unit.embedding_model_id,
                embedding_model_version=unit.embedding_model_version,
                metadata=dict(unit.metadata),
            )
            for unit in command.units
        )
        batches = self._index_batches(command, unit_messages)
        indexed_unit_count = 0
        collection_alias = ""
        for batch_number, batch in enumerate(batches):
            request = self._index_request(
                command,
                batch,
                append_to_asset_version=batch_number > 0,
            )
            try:
                response = await stub.IndexAsset(
                    request,
                    timeout=self._index_timeout_seconds,
                    wait_for_ready=True,
                )
            except grpc.aio.AioRpcError as error:
                if error.code() in {
                    grpc.StatusCode.INVALID_ARGUMENT,
                    grpc.StatusCode.FAILED_PRECONDITION,
                }:
                    raise ValueError(
                        f"C++ Core rejected IndexAsset: {error.details()}"
                    ) from error
                raise CoreUnavailableError(
                    f"C++ Core IndexAsset failed: {error.code().name}"
                ) from error
            if (
                response.request_id != command.request_id
                or response.asset_id != command.asset_id
                or response.asset_version != command.asset_version
                or response.indexed_unit_count != len(batch)
                or not response.collection_alias
            ):
                raise CoreUnavailableError(
                    "C++ Core returned an invalid IndexAsset response"
                )
            if collection_alias and collection_alias != response.collection_alias:
                raise CoreUnavailableError(
                    "C++ Core changed collection during batched IndexAsset"
                )
            collection_alias = response.collection_alias
            indexed_unit_count += response.indexed_unit_count
        return IndexAssetResult(
            request_id=command.request_id,
            asset_id=command.asset_id,
            asset_version=command.asset_version,
            indexed_unit_count=indexed_unit_count,
            collection_alias=collection_alias,
        )

    def _index_request(
        self,
        command: IndexAssetCommand,
        units: tuple[object, ...],
        *,
        append_to_asset_version: bool,
    ):
        assert self._messages is not None
        return self._messages.IndexAssetRequest(
            request_id=command.request_id,
            tenant_id=command.tenant_id,
            acl_id=command.acl_id,
            asset_id=command.asset_id,
            asset_version_id=command.asset_version_id,
            asset_version=command.asset_version,
            object_key=command.object_key,
            units=units,
            append_to_asset_version=append_to_asset_version,
        )

    def _index_batches(
        self,
        command: IndexAssetCommand,
        units: tuple[object, ...],
    ) -> tuple[tuple[object, ...], ...]:
        batches: list[tuple[object, ...]] = []
        current: list[object] = []
        for unit in units:
            current.append(unit)
            request = self._index_request(
                command, tuple(current), append_to_asset_version=True
            )
            if request.ByteSize() <= self._index_batch_max_bytes:
                continue
            current.pop()
            if not current:
                raise ValueError("one normalized unit exceeds the gRPC batch limit")
            batches.append(tuple(current))
            current = [unit]
            request = self._index_request(
                command, tuple(current), append_to_asset_version=True
            )
            if request.ByteSize() > self._index_batch_max_bytes:
                raise ValueError("one normalized unit exceeds the gRPC batch limit")
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    @staticmethod
    def _validate_index_command(command: IndexAssetCommand) -> None:
        if (
            not command.request_id
            or not command.tenant_id
            or not command.acl_id
            or not command.asset_id
            or not command.asset_version_id
            or command.asset_version <= 0
            or not command.object_key
            or not command.units
        ):
            raise ValueError("index asset identity and units must not be empty")
        first = command.units[0]
        if first.modality not in {
            Modality.DOCUMENT,
            Modality.IMAGE,
            Modality.VIDEO,
        }:
            raise ValueError("index unit modality must be document, image, or video")
        if first.modality is Modality.IMAGE and len(command.units) != 1:
            raise ValueError("one image asset version must contain exactly one unit")
        unit_ids: set[str] = set()
        ordinals: set[int] = set()
        for unit in command.units:
            if (
                not unit.unit_id
                or not unit.content
                or len(unit.content.encode("utf-8")) > 65_535
                or len(unit.title.encode("utf-8")) > 2_048
                or unit.ordinal < 0
                or unit.page_number < 0
                or len(unit.content_sha256) != 64
                or not unit.dense_embedding
                or any(not isfinite(value) for value in unit.dense_embedding)
                or not unit.embedding_model_id
                or not unit.embedding_model_version
            ):
                raise ValueError("index unit content and embedding must be valid")
            if (
                unit.modality != first.modality
                or unit.embedding_model_id != first.embedding_model_id
                or unit.embedding_model_version != first.embedding_model_version
                or len(unit.dense_embedding) != len(first.dense_embedding)
            ):
                raise ValueError(
                    "index units must share one modality and embedding model schema"
                )
            metadata_keys: set[str] = set()
            metadata_values: dict[str, str] = {}
            for key, value in unit.metadata:
                if (
                    not key
                    or len(key.encode("utf-8")) > 128
                    or len(value.encode("utf-8")) > 60_000
                    or key in metadata_keys
                ):
                    raise ValueError("index unit metadata must be unique and bounded")
                metadata_keys.add(key)
                metadata_values[key] = value
            if unit.modality is Modality.IMAGE:
                width = metadata_values.get("width", "")
                height = metadata_values.get("height", "")
                if (
                    not unit.title
                    or metadata_values.get("media_type")
                    not in {"image/jpeg", "image/png", "image/webp"}
                    or not width.isascii()
                    or not width.isdecimal()
                    or not height.isascii()
                    or not height.isdecimal()
                    or not 1 <= int(width) <= 4_294_967_295
                    or not 1 <= int(height) <= 4_294_967_295
                    or not metadata_values.get("vision_model_id")
                    or not metadata_values.get("vision_model_version")
                ):
                    raise ValueError("image index metadata and caption must be valid")
            if unit.modality is Modality.VIDEO:
                integer_fields = {
                    name: metadata_values.get(name, "")
                    for name in (
                        "duration_ms",
                        "width",
                        "height",
                        "start_ms",
                        "end_ms",
                        "keyframe_ms",
                    )
                }
                if any(
                    not value.isascii() or not value.isdecimal()
                    for value in integer_fields.values()
                ):
                    raise ValueError("video timestamps and dimensions must be integers")
                duration_ms = int(integer_fields["duration_ms"])
                start_ms = int(integer_fields["start_ms"])
                end_ms = int(integer_fields["end_ms"])
                keyframe_ms = int(integer_fields["keyframe_ms"])
                if (
                    not unit.title
                    or metadata_values.get("media_type")
                    not in {"video/mp4", "video/quicktime", "video/webm"}
                    or not 1 <= duration_ms <= 86_400_000
                    or not 1 <= int(integer_fields["width"]) <= 4_294_967_295
                    or not 1 <= int(integer_fields["height"]) <= 4_294_967_295
                    or int(integer_fields["width"]) > 32_768
                    or int(integer_fields["height"]) > 32_768
                    or not 0 <= start_ms < end_ms <= duration_ms
                    or not start_ms <= keyframe_ms < end_ms
                    or not metadata_values.get("caption")
                    or not metadata_values.get("speech_model_id")
                    or not metadata_values.get("speech_model_version")
                    or not metadata_values.get("vision_model_id")
                    or not metadata_values.get("vision_model_version")
                ):
                    raise ValueError("video index metadata and segment bounds are invalid")
            if unit.unit_id in unit_ids or unit.ordinal in ordinals:
                raise ValueError("index unit IDs and ordinals must be unique")
            unit_ids.add(unit.unit_id)
            ordinals.add(unit.ordinal)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None
        self._index_stub = None
