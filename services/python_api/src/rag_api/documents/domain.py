"""Application-owned document contracts independent of provider SDKs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class DocumentParseError(ValueError):
    """The source bytes cannot be converted into supported text."""


class EmbeddingError(RuntimeError):
    """A generic embedding endpoint failed with retry semantics."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    content: str
    title: str = ""
    page_number: int = 0


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: str
    ordinal: int
    page_number: int
    title: str
    content: str
    content_sha256: str


class EmbeddingModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...
