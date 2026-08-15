"""Standalone Kafka worker routing every supported ingestion media type."""

from __future__ import annotations

import asyncio

from rag_api.config import Settings
from rag_api.core_client import GrpcCoreClient
from rag_api.db.session import create_database_engine, create_session_factory
from rag_api.documents.chunking import DocumentChunker
from rag_api.documents.embeddings import HttpEmbeddingModel
from rag_api.documents.parsers import DocumentParser
from rag_api.documents.processor import DocumentIngestProcessor
from rag_api.images.normalizer import ImageNormalizer
from rag_api.images.processor import ImageIngestProcessor
from rag_api.images.vision import HttpVisionModel
from rag_api.ingestion.acl import SqlAlchemyAssetAclResolver
from rag_api.ingestion.router import RoutingIngestProcessor
from rag_api.kafka.runtime import create_ingest_worker
from rag_api.storage import S3ObjectStore
from rag_api.videos.ffmpeg import FFmpegVideoToolchain
from rag_api.videos.processor import VideoIngestProcessor
from rag_api.videos.speech import HttpSpeechToTextModel


DOCUMENT_MEDIA_TYPES = (
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
)
IMAGE_MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp")
VIDEO_MEDIA_TYPES = ("video/mp4", "video/quicktime", "video/webm")


async def run() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    core_client = GrpcCoreClient(
        settings.core_grpc_target,
        settings.core_grpc_timeout_seconds,
        settings.core_grpc_index_timeout_seconds,
        settings.core_grpc_index_batch_max_bytes,
    )
    object_store = S3ObjectStore(settings)
    acl_resolver = SqlAlchemyAssetAclResolver(session_factory)
    embedding_api_key = (
        settings.embedding_api_key.get_secret_value()
        if settings.embedding_api_key is not None
        else None
    )
    embedding_model = HttpEmbeddingModel(
        endpoint_url=settings.embedding_endpoint_url,
        api_key=embedding_api_key,
        model_id=settings.embedding_model_id,
        model_version=settings.embedding_model_version,
        dimension=settings.embedding_dimension,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    document_processor = DocumentIngestProcessor(
        object_store=object_store,
        parser=DocumentParser(),
        chunker=DocumentChunker(
            max_chars=settings.document_chunk_max_chars,
            overlap_chars=settings.document_chunk_overlap_chars,
        ),
        embedding_model=embedding_model,
        core_client=core_client,
        acl_resolver=acl_resolver,
        max_download_bytes=settings.document_download_max_bytes,
        embedding_batch_size=settings.embedding_batch_size,
    )
    vision_api_key = (
        settings.vision_api_key.get_secret_value()
        if settings.vision_api_key is not None
        else None
    )
    image_processor = ImageIngestProcessor(
        object_store=object_store,
        normalizer=ImageNormalizer(
            max_pixels=settings.image_max_pixels,
            max_dimension=settings.image_model_max_dimension,
            max_output_bytes=settings.image_model_max_bytes,
        ),
        vision_model=HttpVisionModel(
            endpoint_url=settings.vision_endpoint_url,
            api_key=vision_api_key,
            model_id=settings.vision_model_id,
            model_version=settings.vision_model_version,
            timeout_seconds=settings.vision_timeout_seconds,
            caption_max_bytes=settings.vision_caption_max_bytes,
            ocr_max_bytes=settings.vision_ocr_max_bytes,
        ),
        embedding_model=embedding_model,
        core_client=core_client,
        acl_resolver=acl_resolver,
        max_download_bytes=settings.image_download_max_bytes,
    )
    speech_api_key = (
        settings.speech_api_key.get_secret_value()
        if settings.speech_api_key is not None
        else None
    )
    video_processor = VideoIngestProcessor(
        object_store=object_store,
        toolchain=FFmpegVideoToolchain(
            normalizer=ImageNormalizer(
                max_pixels=settings.image_max_pixels,
                max_dimension=settings.image_model_max_dimension,
                max_output_bytes=settings.image_model_max_bytes,
            ),
            ffmpeg_binary=settings.ffmpeg_binary,
            ffprobe_binary=settings.ffprobe_binary,
            command_timeout_seconds=settings.video_command_timeout_seconds,
            max_duration_seconds=settings.video_max_duration_seconds,
            max_pixels=settings.video_max_pixels,
            max_dimension=settings.video_max_dimension,
            audio_chunk_seconds=settings.video_audio_chunk_seconds,
            scene_threshold=settings.video_scene_threshold,
            keyframe_max_gap_seconds=settings.video_keyframe_max_gap_seconds,
            max_keyframes=settings.video_max_keyframes,
        ),
        speech_model=HttpSpeechToTextModel(
            endpoint_url=settings.speech_endpoint_url,
            api_key=speech_api_key,
            model_id=settings.speech_model_id,
            model_version=settings.speech_model_version,
            language=settings.speech_language,
            timeout_seconds=settings.speech_timeout_seconds,
            max_segments=settings.speech_max_segments_per_chunk,
        ),
        vision_model=HttpVisionModel(
            endpoint_url=settings.vision_endpoint_url,
            api_key=vision_api_key,
            model_id=settings.vision_model_id,
            model_version=settings.vision_model_version,
            timeout_seconds=settings.vision_timeout_seconds,
            caption_max_bytes=settings.vision_caption_max_bytes,
            ocr_max_bytes=settings.vision_ocr_max_bytes,
        ),
        embedding_model=embedding_model,
        core_client=core_client,
        acl_resolver=acl_resolver,
        max_download_bytes=settings.video_download_max_bytes,
        embedding_batch_size=settings.embedding_batch_size,
    )
    processor = RoutingIngestProcessor(
        {
            **{media_type: document_processor for media_type in DOCUMENT_MEDIA_TYPES},
            **{media_type: image_processor for media_type in IMAGE_MEDIA_TYPES},
            **{media_type: video_processor for media_type in VIDEO_MEDIA_TYPES},
        }
    )
    worker = create_ingest_worker(
        settings, processor=processor, session_factory=session_factory
    )
    try:
        await worker.run_forever()
    finally:
        await core_client.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
