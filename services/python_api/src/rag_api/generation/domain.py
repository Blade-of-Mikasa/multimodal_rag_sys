"""Application-owned contracts for planning and final answer generation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from rag_api.domain import Modality, SourceScope


ChatRole = Literal["system", "user", "assistant"]
RetrievalScope = Literal["auto", "local", "web", "hybrid"]
AnswerEvent = Literal["planning", "retrieving", "sources", "delta", "done"]


class ChatModelError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class AnswerPipelineError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip() or len(self.content.encode("utf-8")) > 8_000_000:
            raise ValueError("chat message must contain bounded non-blank content")


@dataclass(frozen=True, slots=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    max_output_tokens: int
    temperature: float = 0.1
    response_schema_name: str | None = None
    response_schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("chat request must contain at least one message")
        if not 1 <= self.max_output_tokens <= 65_536:
            raise ValueError("max_output_tokens must be between 1 and 65536")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if (self.response_schema_name is None) != (self.response_schema is None):
            raise ValueError("response schema name and body must be set together")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    text: str
    finish_reason: str
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True, slots=True)
class ChatDelta:
    text: str = ""
    finish_reason: str | None = None
    usage: TokenUsage | None = None


class ChatModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    async def complete(self, request: ChatRequest) -> ChatCompletion: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatDelta]: ...


@dataclass(frozen=True, slots=True)
class AnswerPreferences:
    retrieval_scope: RetrievalScope = "auto"
    modalities: tuple[Modality, ...] = (
        Modality.DOCUMENT,
        Modality.IMAGE,
        Modality.VIDEO,
    )

    def __post_init__(self) -> None:
        if self.retrieval_scope not in {"auto", "local", "web", "hybrid"}:
            raise ValueError("unsupported retrieval scope")
        if not self.modalities or len(set(self.modalities)) != len(self.modalities):
            raise ValueError("modalities must be non-empty and unique")
        if any(modality is Modality.UNSPECIFIED for modality in self.modalities):
            raise ValueError("modalities must be specified")


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    query: str
    source_scope: SourceScope
    modality: Modality

    def __post_init__(self) -> None:
        normalized = self.query.strip()
        if not normalized or len(normalized) > 2_048:
            raise ValueError("planned query must contain between 1 and 2048 characters")
        if self.source_scope is SourceScope.UNSPECIFIED:
            raise ValueError("planned source scope must be specified")
        if self.modality is Modality.UNSPECIFIED:
            raise ValueError("planned modality must be specified")
        if (
            self.source_scope is SourceScope.WEB
            and self.modality is not Modality.DOCUMENT
        ):
            raise ValueError("current web retrieval supports document evidence only")
        object.__setattr__(self, "query", normalized)


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    routes: tuple[PlannedRoute, ...]
    usage: TokenUsage | None = None
    model_id: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.routes) <= 6:
            raise ValueError("retrieval plan must contain between 1 and 6 routes")
        keys = {
            (route.query, route.source_scope, route.modality)
            for route in self.routes
        }
        if len(keys) != len(self.routes):
            raise ValueError("retrieval plan routes must be unique")
        if (self.model_id is None) != (self.model_version is None):
            raise ValueError("planner model ID and version must be set together")
        if self.model_id is not None and (
            not self.model_id.strip()
            or not self.model_version
            or not self.model_version.strip()
            or len(self.model_id) > 256
            or len(self.model_version) > 256
        ):
            raise ValueError("planner model identity must be bounded non-blank text")


class QueryPlanner(Protocol):
    async def plan(
        self,
        query: str,
        preferences: AnswerPreferences,
    ) -> RetrievalPlan: ...


@dataclass(frozen=True, slots=True)
class AnswerUpdate:
    event: AnswerEvent
    data: dict[str, Any] = field(default_factory=dict)
