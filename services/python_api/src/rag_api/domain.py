"""Dependency-free domain types mirrored by the v1 Protobuf contract.

The Protobuf schema remains the cross-process source of truth. These types keep
surface-layer validation testable before generated gRPC stubs are introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from math import isfinite
import re
from urllib.parse import urlsplit


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
MAX_EXTERNAL_EVIDENCE_COUNT = 1_200
MIN_CONTEXT_TOKEN_BUDGET = 512
MAX_CONTEXT_TOKEN_BUDGET = 1_000_000
MIN_EVIDENCE_TOKEN_BUDGET = 256


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
        elif self.source_scope is not SourceScope.LOCAL:
            errors.append(
                "Core retrieval routes must use LOCAL; pass web sources as "
                "external evidence"
            )
        if self.modality is Modality.UNSPECIFIED:
            errors.append("modality must be specified")
        if not 1 <= self.top_k <= MAX_TOP_K:
            errors.append("top_k must be between 1 and 200")
        if not MIN_TIMEOUT_MS <= self.timeout_ms <= MAX_TIMEOUT_MS:
            errors.append("timeout_ms must be between 100 and 30000")
        if (
            self.source_scope is SourceScope.LOCAL
            and self.modality in {Modality.DOCUMENT, Modality.IMAGE, Modality.VIDEO}
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


@dataclass(frozen=True, slots=True)
class ExternalEvidence:
    evidence_id: str
    content: str
    modality: Modality
    source_scope: SourceScope
    title: str = ""
    source: str = ""
    url: str = ""
    published_at_unix_ms: int = 0
    retrieved_at_unix_ms: int = 0
    score: float = 0.0
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    content_sha256: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not 1 <= _utf8_size(self.evidence_id) <= 256:
            errors.append("evidence_id must contain between 1 and 256 bytes")
        if not 1 <= _utf8_size(self.content) <= 1_000_000:
            errors.append("content must contain between 1 and 1000000 bytes")
        if self.modality is Modality.UNSPECIFIED:
            errors.append("evidence modality must be specified")
        if self.source_scope is not SourceScope.WEB:
            errors.append("external evidence must use WEB source_scope")
        if _utf8_size(self.title) > 4_096:
            errors.append("evidence title exceeds its byte limit")
        if _utf8_size(self.source) > 16_384 or _utf8_size(self.url) > 16_384:
            errors.append("evidence source fields exceed their byte limits")
        if not _is_http_url(self.url):
            errors.append("web evidence must contain an HTTP(S) URL")
        if self.published_at_unix_ms < 0 or self.retrieved_at_unix_ms < 0:
            errors.append("evidence timestamps must not be negative")
        if not isfinite(self.score):
            errors.append("evidence score must be finite")
        if self.content_sha256 and not re.fullmatch(
            r"[0-9a-f]{64}", self.content_sha256
        ):
            errors.append("content_sha256 must be lowercase hexadecimal")
        if len(self.metadata) > 64:
            errors.append("evidence metadata must not exceed 64 entries")
        if len(dict(self.metadata)) != len(self.metadata):
            errors.append("evidence metadata keys must be unique")
        for key, value in self.metadata:
            if not 1 <= _utf8_size(key) <= 128 or _utf8_size(value) > 16_384:
                errors.append(
                    "evidence metadata key or value exceeds its byte limit"
                )
                break
        return errors


@dataclass(frozen=True)
class ExecutionPlan:
    request_id: str
    routes: tuple[RetrievalRoute, ...] = field(default_factory=tuple)
    user_id: str = ""
    conversation_id: str = ""
    tenant_id: str = ""
    allowed_acl_ids: tuple[str, ...] = field(default_factory=tuple)
    external_evidence: tuple[ExternalEvidence, ...] = field(default_factory=tuple)
    context_token_budget: int = 12_000
    max_evidence_tokens: int = 2_000

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.request_id:
            errors.append("request_id must not be empty")
        if not self.tenant_id:
            errors.append("tenant_id must not be empty")
        if any(
            route.source_scope is SourceScope.LOCAL for route in self.routes
        ) and not self.allowed_acl_ids:
            errors.append("allowed_acl_ids must not be empty for local retrieval")
        if not self.routes and not self.external_evidence:
            errors.append("at least one route or external evidence item is required")
        if len(self.routes) > MAX_ROUTE_COUNT:
            errors.append("route count must not exceed 6")
        if len(self.external_evidence) > MAX_EXTERNAL_EVIDENCE_COUNT:
            errors.append("external evidence count must not exceed 1200")
        if not (
            MIN_CONTEXT_TOKEN_BUDGET
            <= self.context_token_budget
            <= MAX_CONTEXT_TOKEN_BUDGET
        ):
            errors.append("context_token_budget must be between 512 and 1000000")
        if not (
            MIN_EVIDENCE_TOKEN_BUDGET
            <= self.max_evidence_tokens
            <= self.context_token_budget
        ):
            errors.append(
                "max_evidence_tokens must be between 256 and context_token_budget"
            )

        route_ids: set[str] = set()
        for route in self.routes:
            errors.extend(route.validate())
            if route.route_id and route.route_id in route_ids:
                errors.append("route_id must be unique")
            route_ids.add(route.route_id)
        for evidence in self.external_evidence:
            errors.extend(evidence.validate())
        return errors


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _is_http_url(value: str) -> bool:
    if any(character in value for character in ("\r", "\n", "\x00")):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )
