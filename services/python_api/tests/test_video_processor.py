from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import unittest
from uuid import UUID

from rag_api.core_client import IndexAssetResult
from rag_api.domain import Modality
from rag_api.images.domain import NormalizedImage, VisionAnalysis
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import PermanentIngestError
from rag_api.storage import DownloadedObject
from rag_api.videos.domain import (
    AudioChunk,
    Keyframe,
    TranscriptSegment,
    VideoMedia,
)
from rag_api.videos.processor import VideoIngestProcessor


PAYLOAD = b"bounded fake mp4 payload"
IMAGE = NormalizedImage(
    payload=b"jpeg",
    media_type="image/jpeg",
    width=1280,
    height=720,
    model_width=1280,
    model_height=720,
)


def event(*, checksum: str | None = None) -> IngestTaskEvent:
    return IngestTaskEvent(
        event_id=UUID("10000000-0000-4000-8000-000000000009"),
        event_type="ingest.requested",
        task_id=UUID("20000000-0000-4000-8000-000000000009"),
        dedupe_key="asset-video-version-1:ingest",
        tenant_id="tenant-1",
        asset_id=UUID("30000000-0000-4000-8000-000000000009"),
        asset_version_id=UUID("40000000-0000-4000-8000-000000000009"),
        version_number=1,
        object_key="tenant-1/asset-video/v1/video.mp4",
        content_type="video/mp4",
        size_bytes=len(PAYLOAD),
        content_sha256=checksum or sha256(PAYLOAD).hexdigest(),
        attempt=0,
        max_attempts=5,
    )


class FakeObjectStore:
    async def download_to_file(
        self, object_key: str, *, destination: Path, max_bytes: int
    ) -> DownloadedObject:
        self.call = (object_key, destination.suffix, max_bytes)
        destination.write_bytes(PAYLOAD)
        return DownloadedObject(
            size_bytes=len(PAYLOAD), content_sha256=sha256(PAYLOAD).hexdigest()
        )


class FakeToolchain:
    async def probe(self, source: Path, media_type: str) -> VideoMedia:
        self.probe_call = (source.suffix, media_type, source.read_bytes())
        return VideoMedia(
            media_type="video/mp4",
            format_name="mov,mp4",
            duration_ms=90_000,
            width=1920,
            height=1080,
            has_audio=True,
        )

    async def extract_audio_chunks(
        self, source: Path, media: VideoMedia, output_dir: Path
    ) -> tuple[AudioChunk, ...]:
        path = output_dir / "audio.wav"
        path.write_bytes(b"wav")
        return (AudioChunk(path=path, start_ms=0, duration_ms=90_000),)

    async def extract_keyframes(
        self, source: Path, media: VideoMedia, output_dir: Path
    ) -> tuple[Keyframe, ...]:
        return (
            Keyframe(timestamp_ms=0, image=IMAGE),
            Keyframe(timestamp_ms=60_000, image=IMAGE),
        )


class FakeSpeechModel:
    model_id = "speech-general"
    model_version = "v1"

    async def transcribe(self, audio: AudioChunk):
        self.audio = audio
        return (
            TranscriptSegment(10_000, 20_000, "dense and sparse retrieval"),
            TranscriptSegment(65_000, 75_000, "RRF fusion"),
        )


class FakeVisionModel:
    model_id = "vision-general"
    model_version = "v1"

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, image: NormalizedImage) -> VisionAnalysis:
        self.calls += 1
        return VisionAnalysis(
            caption=f"Architecture slide {self.calls}",
            ocr_text="MILVUS" if self.calls == 1 else "KAFKA",
        )


class FakeEmbeddingModel:
    model_id = "embedding-general"
    model_version = "v1"

    async def embed(self, texts):
        self.texts = tuple(texts)
        return tuple((1.0, float(index)) for index, _ in enumerate(texts))


class FakeAclResolver:
    async def resolve_acl_id(self, ingest_event: IngestTaskEvent) -> str:
        return "acl-video"


class FakeCoreClient:
    async def index_asset(self, command):
        self.command = command
        return IndexAssetResult(
            request_id=command.request_id,
            asset_id=command.asset_id,
            asset_version=command.asset_version,
            indexed_unit_count=len(command.units),
            collection_alias="rag_video_v1_test_2",
        )


class VideoProcessorTest(unittest.IsolatedAsyncioTestCase):
    def processor(self):
        core = FakeCoreClient()
        embedding = FakeEmbeddingModel()
        return (
            VideoIngestProcessor(
                object_store=FakeObjectStore(),
                toolchain=FakeToolchain(),
                speech_model=FakeSpeechModel(),
                vision_model=FakeVisionModel(),
                embedding_model=embedding,
                core_client=core,
                acl_resolver=FakeAclResolver(),
                max_download_bytes=1_000_000,
                embedding_batch_size=8,
            ),
            core,
            embedding,
        )

    async def test_builds_time_bounded_video_units_with_model_provenance(self) -> None:
        processor, core, embedding = self.processor()

        await processor.process(event())

        self.assertEqual(2, len(core.command.units))
        first, second = core.command.units
        first_metadata = dict(first.metadata)
        second_metadata = dict(second.metadata)
        self.assertEqual(Modality.VIDEO, first.modality)
        self.assertEqual(("0", "60000", "0"), (
            first_metadata["start_ms"],
            first_metadata["end_ms"],
            first_metadata["keyframe_ms"],
        ))
        self.assertEqual(("60000", "90000", "60000"), (
            second_metadata["start_ms"],
            second_metadata["end_ms"],
            second_metadata["keyframe_ms"],
        ))
        self.assertIn("dense and sparse retrieval", first.content)
        self.assertNotIn("RRF fusion", first.content)
        self.assertIn("RRF fusion", second.content)
        self.assertEqual("speech-general", first_metadata["speech_model_id"])
        self.assertEqual("vision-general", first_metadata["vision_model_id"])
        self.assertEqual(tuple(item.content for item in core.command.units), embedding.texts)
        self.assertNotEqual(first.unit_id, second.unit_id)

    async def test_checksum_mismatch_is_permanent_before_ffmpeg(self) -> None:
        processor, _, _ = self.processor()

        with self.assertRaises(PermanentIngestError) as raised:
            await processor.process(event(checksum="0" * 64))

        self.assertEqual("VIDEO_CHECKSUM_MISMATCH", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
