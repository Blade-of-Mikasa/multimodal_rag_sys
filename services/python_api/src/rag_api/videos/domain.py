"""Application-owned video contracts independent of FFmpeg and model SDKs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rag_api.images.domain import NormalizedImage


class VideoProcessingError(RuntimeError):
    """Video probing or extraction failed with explicit retry semantics."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class SpeechToTextError(RuntimeError):
    """A generic speech endpoint failed with explicit retry semantics."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class VideoMedia:
    media_type: str
    format_name: str
    duration_ms: int
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True, slots=True)
class AudioChunk:
    path: Path
    start_ms: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class Keyframe:
    timestamp_ms: int
    image: NormalizedImage


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


class SpeechToTextModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    async def transcribe(self, audio: AudioChunk) -> tuple[TranscriptSegment, ...]: ...


class VideoToolchain(Protocol):
    async def probe(self, source: Path, media_type: str) -> VideoMedia: ...

    async def extract_audio_chunks(
        self, source: Path, media: VideoMedia, output_dir: Path
    ) -> tuple[AudioChunk, ...]: ...

    async def extract_keyframes(
        self, source: Path, media: VideoMedia, output_dir: Path
    ) -> tuple[Keyframe, ...]: ...
