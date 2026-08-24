from __future__ import annotations

import unittest

from rag_api.core_client import GrpcCoreClient, IndexAssetCommand, IndexUnit
from rag_api.domain import Modality


def command(unit: IndexUnit) -> IndexAssetCommand:
    return IndexAssetCommand(
        request_id="request-1",
        tenant_id="tenant-1",
        acl_id="acl-1",
        asset_id="asset-1",
        asset_version_id="version-1",
        asset_version=1,
        object_key="tenant-1/asset-1/v1/image.png",
        units=(unit,),
    )


def image_unit(**overrides) -> IndexUnit:
    values = {
        "unit_id": "image-1",
        "modality": Modality.IMAGE,
        "content": "A red bicycle",
        "title": "A red bicycle",
        "ordinal": 0,
        "page_number": 0,
        "content_sha256": "a" * 64,
        "dense_embedding": (1.0, 0.0),
        "embedding_model_id": "embedding-general",
        "embedding_model_version": "v1",
        "metadata": (
            ("media_type", "image/png"),
            ("width", "800"),
            ("height", "600"),
            ("vision_model_id", "vision-general"),
            ("vision_model_version", "v1"),
        ),
    }
    values.update(overrides)
    return IndexUnit(**values)


def video_unit(**overrides) -> IndexUnit:
    values = {
        "unit_id": "segment-1",
        "modality": Modality.VIDEO,
        "content": "Caption and time-aligned transcript",
        "title": "Architecture segment",
        "ordinal": 0,
        "page_number": 0,
        "content_sha256": "b" * 64,
        "dense_embedding": (1.0, 0.0),
        "embedding_model_id": "embedding-general",
        "embedding_model_version": "v1",
        "metadata": (
            ("media_type", "video/mp4"),
            ("duration_ms", "90000"),
            ("width", "1920"),
            ("height", "1080"),
            ("start_ms", "0"),
            ("end_ms", "60000"),
            ("keyframe_ms", "0"),
            ("caption", "Architecture segment"),
            ("ocr_text", "MILVUS"),
            ("transcript", "time-aligned transcript"),
            ("speech_model_id", "speech-general"),
            ("speech_model_version", "v1"),
            ("vision_model_id", "vision-general"),
            ("vision_model_version", "v1"),
        ),
    }
    values.update(overrides)
    return IndexUnit(**values)


class CoreIndexContractTest(unittest.TestCase):
    def test_accepts_bounded_image_metadata(self) -> None:
        GrpcCoreClient._validate_index_command(command(image_unit()))

    def test_accepts_bounded_video_segment_metadata(self) -> None:
        GrpcCoreClient._validate_index_command(command(video_unit()))

        invalid = video_unit(
            metadata=tuple(
                (key, "90001" if key == "end_ms" else value)
                for key, value in video_unit().metadata
            )
        )
        with self.assertRaises(ValueError):
            GrpcCoreClient._validate_index_command(command(invalid))

    def test_rejects_duplicate_metadata_and_mixed_modalities(self) -> None:
        with self.assertRaises(ValueError):
            GrpcCoreClient._validate_index_command(
                command(
                    image_unit(
                        metadata=(("media_type", "image/png"), ("media_type", "x"))
                    )
                )
            )
        document = image_unit(modality=Modality.DOCUMENT, unit_id="chunk-1")
        mixed = command(image_unit())
        mixed = IndexAssetCommand(
            request_id=mixed.request_id,
            tenant_id=mixed.tenant_id,
            acl_id=mixed.acl_id,
            asset_id=mixed.asset_id,
            asset_version_id=mixed.asset_version_id,
            asset_version=mixed.asset_version,
            object_key=mixed.object_key,
            units=(mixed.units[0], document),
        )
        with self.assertRaises(ValueError):
            GrpcCoreClient._validate_index_command(mixed)


if __name__ == "__main__":
    unittest.main()
