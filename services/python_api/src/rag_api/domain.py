"""Dependency-free domain types mirrored by the v1 Protobuf contract.

The Protobuf schema remains the cross-process source of truth. These types keep
surface-layer validation testable before generated gRPC stubs are introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from math import isfinite


class SourceScope(IntEnum):
    UNSPECIFIED = 0
    LOCAL = 1
    WEB = 2


class Modality(IntEnum):
    UNSPECIFIED = 0
    DOCUMENT = 1
    IMAGE = 2
    VIDEO = 3


MAX_ROUTE_COUNT = 6
MAX_TOP_K = 200
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 30_000
MAX_EMBEDDING_DIMENSION = 65_536


@dataclass(frozen=True)
class RetrievalRoute:
    route_id: str
    query: str
    source_scope: SourceScope
    modality: Modality
    top_k: int = 10
    timeout_ms: int = 2_000
    dense_embedding: tuple[float, ...] = field(default_factory=tuple)
    embedding_model_id: str = ""
    embedding_model_version: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.route_id:
            errors.append("route_id must not be empty")
        if not self.query:
            errors.append("query must not be empty")
        if self.source_scope is SourceScope.UNSPECIFIED:
            errors.append("source_scope must be specified")
        if self.modality is Modality.UNSPECIFIED:
            errors.append("modality must be specified")
        if not 1 <= self.top_k <= MAX_TOP_K:
            errors.append("top_k must be between 1 and 200")
        if not MIN_TIMEOUT_MS <= self.timeout_ms <= MAX_TIMEOUT_MS:
            errors.append("timeout_ms must be between 100 and 30000")
        if (
            self.source_scope is SourceScope.LOCAL
            and self.modality in {Modality.DOCUMENT, Modality.IMAGE}
        ):
            if not 1 <= len(self.dense_embedding) <= MAX_EMBEDDING_DIMENSION:
                errors.append(
                    "local route dense_embedding dimension must be between "
                    "1 and 65536"
                )
            if not self.embedding_model_id or not self.embedding_model_version:
                errors.append(
                    "local route embedding model identity must be specified"
                )
            if any(not isfinite(value) for value in self.dense_embedding):
                errors.append("dense_embedding values must be finite")
        return errors


@dataclass(frozen=True)
class ExecutionPlan:
    request_id: str
    routes: tuple[RetrievalRoute, ...]
    user_id: str = ""
    conversation_id: str = ""
    tenant_id: str = ""
    allowed_acl_ids: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.request_id:
            errors.append("request_id must not be empty")
        if not self.tenant_id:
            errors.append("tenant_id must not be empty")
        if not self.allowed_acl_ids:
            errors.append("allowed_acl_ids must not be empty")
        if not self.routes:
            errors.append("at least one route is required")
        if len(self.routes) > MAX_ROUTE_COUNT:
            errors.append("route count must not exceed 6")

        route_ids: set[str] = set()
        for route in self.routes:
            errors.extend(route.validate())
            if route.route_id and route.route_id in route_ids:
                errors.append("route_id must be unique")
            route_ids.add(route.route_id)
        return errors
