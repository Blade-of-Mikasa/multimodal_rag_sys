"""Application-owned image contracts independent of provider SDKs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ImageNormalizationError(ValueError):
    """The source bytes are not a safe supported still image."""


class VisionError(RuntimeError):
    """A generic vision endpoint failed with retry semantics."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    payload: bytes
    media_type: str
    width: int
    height: int
    model_width: int
    model_height: int


@dataclass(frozen=True, slots=True)
class VisionAnalysis:
    caption: str
    ocr_text: str


class VisionModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    async def analyze(self, image: NormalizedImage) -> VisionAnalysis: ...
