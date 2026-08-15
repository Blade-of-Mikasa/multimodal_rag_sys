"""Kafka video processor composing FFmpeg, ASR, Vision, and C++ indexing."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid5

from rag_api.core_client import (
    CoreClient,
    CoreUnavailableError,
    IndexAssetCommand,
    IndexUnit,
)
from rag_api.documents.domain import EmbeddingError, EmbeddingModel
from rag_api.domain import Modality
from rag_api.images.domain import VisionAnalysis, VisionError, VisionModel
from rag_api.ingestion.domain import (
    AssetAclResolver,
    AssetIdentityError,
    truncate_utf8,
)
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import PermanentIngestError, RetryableIngestError
from rag_api.storage import (
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    ObjectTooLargeError,
)
from rag_api.videos.domain import (
    Keyframe,
    SpeechToTextError,
    SpeechToTextModel,
    TranscriptSegment,
    VideoMedia,
    VideoProcessingError,
    VideoToolchain,
)


VIDEO_SEGMENT_NAMESPACE = UUID("69653e37-9942-4d4a-91bb-203fc18ea664")
_VIDEO_SUFFIXES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


@dataclass(frozen=True, slots=True)
class _Segment:
    unit_id: str
    ordinal: int
    start_ms: int
    end_ms: int
    keyframe_ms: int
    caption: str
    ocr_text: str
    transcript: str
    title: str
    content: str
    content_sha256: str


class VideoIngestProcessor:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        toolchain: VideoToolchain,
        speech_model: SpeechToTextModel,
        vision_model: VisionModel,
        embedding_model: EmbeddingModel,
        core_client: CoreClient,
        acl_resolver: AssetAclResolver,
        max_download_bytes: int,
        embedding_batch_size: int,
    ) -> None:
        self._object_store = object_store
        self._toolchain = toolchain
        self._speech_model = speech_model
        self._vision_model = vision_model
        self._embedding_model = embedding_model
        self._core_client = core_client
        self._acl_resolver = acl_resolver
        self._max_download_bytes = max_download_bytes
        self._embedding_batch_size = embedding_batch_size

    async def process(self, event: IngestTaskEvent) -> None:
        media_type = event.content_type.partition(";")[0].strip().lower()
        suffix = _VIDEO_SUFFIXES.get(media_type)
        if suffix is None:
            raise PermanentIngestError(
                "UNSUPPORTED_VIDEO_TYPE", f"unsupported video media type: {media_type}"
            )
        try:
            acl_id = await self._acl_resolver.resolve_acl_id(event)
        except AssetIdentityError as error:
            raise PermanentIngestError("ASSET_IDENTITY_INVALID", str(error)) from error

        with TemporaryDirectory(prefix="rag-video-") as temporary:
            root = Path(temporary)
            source = root / f"source{suffix}"
            try:
                downloaded = await self._object_store.download_to_file(
                    event.object_key,
                    destination=source,
                    max_bytes=self._max_download_bytes,
                )
            except ObjectTooLargeError as error:
                raise PermanentIngestError(
                    "VIDEO_TOO_LARGE", "video exceeds the ingestion byte limit"
                ) from error
            except ObjectNotFoundError as error:
                raise RetryableIngestError(
                    "VIDEO_NOT_FOUND", "video object is not available"
                ) from error
            except ObjectStoreError as error:
                raise RetryableIngestError(
                    "OBJECT_STORE_UNAVAILABLE", str(error)
                ) from error
            if downloaded.size_bytes != event.size_bytes:
                raise PermanentIngestError(
                    "VIDEO_SIZE_MISMATCH", "downloaded video size does not match event"
                )
            if downloaded.content_sha256 != event.content_sha256:
                raise PermanentIngestError(
                    "VIDEO_CHECKSUM_MISMATCH",
                    "downloaded video checksum does not match event",
                )

            try:
                media = await self._toolchain.probe(source, media_type)
                audio_dir = root / "audio"
                audio_dir.mkdir()
                chunks = await self._toolchain.extract_audio_chunks(
                    source, media, audio_dir
                )
                transcript_segments: list[TranscriptSegment] = []
                for chunk in chunks:
                    transcript_segments.extend(
                        await self._speech_model.transcribe(chunk)
                    )
                frames_dir = root / "keyframe-work"
                frames_dir.mkdir()
                keyframes = await self._toolchain.extract_keyframes(
                    source, media, frames_dir
                )
            except VideoProcessingError as error:
                error_type = (
                    RetryableIngestError
                    if error.retryable
                    else PermanentIngestError
                )
                raise error_type("VIDEO_NORMALIZATION_FAILED", str(error)) from error
            except SpeechToTextError as error:
                error_type = (
                    RetryableIngestError
                    if error.retryable
                    else PermanentIngestError
                )
                raise error_type("SPEECH_TO_TEXT_FAILED", str(error)) from error

            analyses: list[VisionAnalysis] = []
            try:
                for keyframe in keyframes:
                    analyses.append(await self._vision_model.analyze(keyframe.image))
            except VisionError as error:
                error_type = (
                    RetryableIngestError
                    if error.retryable
                    else PermanentIngestError
                )
                raise error_type("VISION_ANALYSIS_FAILED", str(error)) from error

            try:
                segments = _build_segments(
                    asset_version_id=str(event.asset_version_id),
                    media=media,
                    keyframes=keyframes,
                    analyses=tuple(analyses),
                    transcript_segments=tuple(transcript_segments),
                )
            except ValueError as error:
                raise PermanentIngestError(
                    "VIDEO_SEGMENTATION_FAILED", str(error)
                ) from error

            embeddings: list[tuple[float, ...]] = []
            try:
                for start in range(0, len(segments), self._embedding_batch_size):
                    batch = segments[start : start + self._embedding_batch_size]
                    embeddings.extend(
                        await self._embedding_model.embed(
                            tuple(segment.content for segment in batch)
                        )
                    )
            except EmbeddingError as error:
                error_type = (
                    RetryableIngestError
                    if error.retryable
                    else PermanentIngestError
                )
                raise error_type("EMBEDDING_FAILED", str(error)) from error
            if len(embeddings) != len(segments):
                raise PermanentIngestError(
                    "EMBEDDING_COUNT_MISMATCH",
                    "embedding response count does not match video segments",
                )

        command = IndexAssetCommand(
            request_id=str(event.event_id),
            tenant_id=event.tenant_id,
            acl_id=acl_id,
            asset_id=str(event.asset_id),
            asset_version_id=str(event.asset_version_id),
            asset_version=event.version_number,
            object_key=event.object_key,
            units=tuple(
                IndexUnit(
                    unit_id=segment.unit_id,
                    modality=Modality.VIDEO,
                    content=segment.content,
                    title=segment.title,
                    ordinal=segment.ordinal,
                    page_number=0,
                    content_sha256=segment.content_sha256,
                    dense_embedding=embedding,
                    embedding_model_id=self._embedding_model.model_id,
                    embedding_model_version=self._embedding_model.model_version,
                    metadata=(
                        ("media_type", media.media_type),
                        ("duration_ms", str(media.duration_ms)),
                        ("width", str(media.width)),
                        ("height", str(media.height)),
                        ("start_ms", str(segment.start_ms)),
                        ("end_ms", str(segment.end_ms)),
                        ("keyframe_ms", str(segment.keyframe_ms)),
                        ("caption", segment.caption),
                        ("ocr_text", segment.ocr_text),
                        ("transcript", segment.transcript),
                        ("speech_model_id", self._speech_model.model_id),
                        ("speech_model_version", self._speech_model.model_version),
                        ("vision_model_id", self._vision_model.model_id),
                        ("vision_model_version", self._vision_model.model_version),
                    ),
                )
                for segment, embedding in zip(segments, embeddings, strict=True)
            ),
        )
        try:
            result = await self._core_client.index_asset(command)
        except CoreUnavailableError as error:
            raise RetryableIngestError("INDEX_CORE_UNAVAILABLE", str(error)) from error
        except ValueError as error:
            raise PermanentIngestError("INDEX_CONTRACT_INVALID", str(error)) from error
        if result.indexed_unit_count != len(segments):
            raise RetryableIngestError(
                "INDEX_COUNT_MISMATCH",
                "C++ Core indexed an unexpected video segment count",
            )


def _build_segments(
    *,
    asset_version_id: str,
    media: VideoMedia,
    keyframes: tuple[Keyframe, ...],
    analyses: tuple[VisionAnalysis, ...],
    transcript_segments: tuple[TranscriptSegment, ...],
) -> tuple[_Segment, ...]:
    if not keyframes or len(keyframes) != len(analyses):
        raise ValueError("video must produce matching keyframes and analyses")
    if keyframes[0].timestamp_ms > 1_000:
        raise ValueError("the first keyframe must represent the beginning of the video")
    segments: list[_Segment] = []
    for ordinal, (keyframe, analysis) in enumerate(
        zip(keyframes, analyses, strict=True)
    ):
        start_ms = 0 if ordinal == 0 else keyframe.timestamp_ms
        end_ms = (
            keyframes[ordinal + 1].timestamp_ms
            if ordinal + 1 < len(keyframes)
            else media.duration_ms
        )
        if not 0 <= start_ms < end_ms <= media.duration_ms:
            raise ValueError("keyframe timestamps must be strictly increasing")
        transcript = " ".join(
            item.text
            for item in transcript_segments
            if item.start_ms < end_ms and item.end_ms > start_ms
        )
        transcript = truncate_utf8(transcript, 49_152)
        caption = truncate_utf8(analysis.caption.strip(), 8_192)
        ocr_text = truncate_utf8(analysis.ocr_text.strip(), 49_152)
        parts = [f"Caption: {caption}"]
        if ocr_text:
            parts.append(f"OCR: {ocr_text}")
        if transcript:
            parts.append(f"Transcript: {transcript}")
        content = truncate_utf8("\n".join(parts), 60_000)
        digest = sha256(content.encode("utf-8")).hexdigest()
        unit_id = str(
            uuid5(
                VIDEO_SEGMENT_NAMESPACE,
                f"{asset_version_id}:{ordinal}:{start_ms}:{end_ms}:{digest}",
            )
        )
        segments.append(
            _Segment(
                unit_id=unit_id,
                ordinal=ordinal,
                start_ms=start_ms,
                end_ms=end_ms,
                keyframe_ms=keyframe.timestamp_ms,
                caption=caption,
                ocr_text=ocr_text,
                transcript=transcript,
                title=truncate_utf8(caption or transcript, 2_048),
                content=content,
                content_sha256=digest,
            )
        )
    return tuple(segments)
