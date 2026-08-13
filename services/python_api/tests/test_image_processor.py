from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import unittest
from uuid import UUID

from PIL import Image

from rag_api.core_client import IndexAssetResult
from rag_api.domain import Modality
from rag_api.images.domain import VisionAnalysis
from rag_api.images.normalizer import ImageNormalizer
from rag_api.images.processor import ImageIngestProcessor
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import PermanentIngestError


def png_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 16), "red").save(output, format="PNG")
    return output.getvalue()


PAYLOAD = png_payload()


def event(*, checksum: str | None = None) -> IngestTaskEvent:
    return IngestTaskEvent(
        event_id=UUID("10000000-0000-4000-8000-000000000008"),
        event_type="ingest.requested",
        task_id=UUID("20000000-0000-4000-8000-000000000008"),
        dedupe_key="asset-image-version-1:ingest",
        tenant_id="tenant-1",
        asset_id=UUID("30000000-0000-4000-8000-000000000008"),
        asset_version_id=UUID("40000000-0000-4000-8000-000000000008"),
        version_number=1,
        object_key="tenant-1/asset-image/v1/image.png",
        content_type="image/png",
        size_bytes=len(PAYLOAD),
        content_sha256=checksum or sha256(PAYLOAD).hexdigest(),
        attempt=0,
        max_attempts=5,
    )


class FakeObjectStore:
    async def download(self, object_key: str, *, max_bytes: int) -> bytes:
        self.call = (object_key, max_bytes)
        return PAYLOAD


class FakeAclResolver:
    async def resolve_acl_id(self, ingest_event: IngestTaskEvent) -> str:
        return "acl-image"


class FakeVisionModel:
    model_id = "vision-general"
    model_version = "v1"

    async def analyze(self, image):
        self.image = image
        return VisionAnalysis(caption="A red sign", ocr_text="OPEN")


class FakeEmbeddingModel:
    model_id = "embedding-general"
    model_version = "v1"
    dimension = 2

    async def embed(self, texts):
        self.texts = tuple(texts)
        return ((1.0, 0.0),)


class FakeCoreClient:
    async def index_asset(self, command):
        self.command = command
        return IndexAssetResult(
            request_id=command.request_id,
            asset_id=command.asset_id,
            asset_version=command.asset_version,
            indexed_unit_count=1,
            collection_alias="rag_image_v1_test_2",
        )


class ImageProcessorTest(unittest.IsolatedAsyncioTestCase):
    def processor(self):
        core = FakeCoreClient()
        embedding = FakeEmbeddingModel()
        return (
            ImageIngestProcessor(
                object_store=FakeObjectStore(),
                normalizer=ImageNormalizer(
                    max_pixels=1_000_000,
                    max_dimension=512,
                    max_output_bytes=1_000_000,
                ),
                vision_model=FakeVisionModel(),
                embedding_model=embedding,
                core_client=core,
                acl_resolver=FakeAclResolver(),
                max_download_bytes=1_000_000,
            ),
            core,
            embedding,
        )

    async def test_builds_one_image_index_unit_with_model_provenance(self) -> None:
        processor, core, embedding = self.processor()

        await processor.process(event())

        unit = core.command.units[0]
        metadata = dict(unit.metadata)
        self.assertEqual(Modality.IMAGE, unit.modality)
        self.assertEqual("A red sign\nOCR:\nOPEN", unit.content)
        self.assertEqual(("A red sign\nOCR:\nOPEN",), embedding.texts)
        self.assertEqual("image/png", metadata["media_type"])
        self.assertEqual("32", metadata["width"])
        self.assertEqual("16", metadata["height"])
        self.assertEqual("vision-general", metadata["vision_model_id"])

    async def test_checksum_mismatch_is_permanent(self) -> None:
        processor, _, _ = self.processor()

        with self.assertRaises(PermanentIngestError) as raised:
            await processor.process(event(checksum="0" * 64))

        self.assertEqual("IMAGE_CHECKSUM_MISMATCH", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
