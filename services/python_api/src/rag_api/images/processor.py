"""Kafka image processor composing Vision, Embedding, and C++ indexing."""

from __future__ import annotations

from hashlib import sha256

from rag_api.core_client import (
    CoreClient,
    CoreUnavailableError,
    IndexAssetCommand,
    IndexUnit,
)
from rag_api.documents.domain import EmbeddingError, EmbeddingModel
from rag_api.images.domain import ImageNormalizationError, VisionError, VisionModel
from rag_api.images.normalizer import ImageNormalizer
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
from rag_api.domain import Modality


class ImageIngestProcessor:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        normalizer: ImageNormalizer,
        vision_model: VisionModel,
        embedding_model: EmbeddingModel,
        core_client: CoreClient,
        acl_resolver: AssetAclResolver,
        max_download_bytes: int,
    ) -> None:
        self._object_store = object_store
        self._normalizer = normalizer
        self._vision_model = vision_model
        self._embedding_model = embedding_model
        self._core_client = core_client
        self._acl_resolver = acl_resolver
        self._max_download_bytes = max_download_bytes

    async def process(self, event: IngestTaskEvent) -> None:
        try:
            acl_id = await self._acl_resolver.resolve_acl_id(event)
        except AssetIdentityError as error:
            raise PermanentIngestError("ASSET_IDENTITY_INVALID", str(error)) from error

        try:
            payload = await self._object_store.download(
                event.object_key, max_bytes=self._max_download_bytes
            )
        except ObjectTooLargeError as error:
            raise PermanentIngestError(
                "IMAGE_TOO_LARGE", "image exceeds the ingestion byte limit"
            ) from error
        except ObjectNotFoundError as error:
            raise RetryableIngestError(
                "IMAGE_NOT_FOUND", "image object is not available"
            ) from error
        except ObjectStoreError as error:
            raise RetryableIngestError(
                "OBJECT_STORE_UNAVAILABLE", str(error)
            ) from error

        if len(payload) != event.size_bytes:
            raise PermanentIngestError(
                "IMAGE_SIZE_MISMATCH", "downloaded image size does not match event"
            )
        if sha256(payload).hexdigest() != event.content_sha256:
            raise PermanentIngestError(
                "IMAGE_CHECKSUM_MISMATCH",
                "downloaded image checksum does not match event",
            )
        try:
            image = self._normalizer.normalize(payload, event.content_type)
        except ImageNormalizationError as error:
            raise PermanentIngestError(
                "IMAGE_NORMALIZATION_FAILED", str(error)
            ) from error

        try:
            analysis = await self._vision_model.analyze(image)
        except VisionError as error:
            error_type = (
                RetryableIngestError if error.retryable else PermanentIngestError
            )
            raise error_type("VISION_ANALYSIS_FAILED", str(error)) from error

        semantic_text = analysis.caption
        if analysis.ocr_text:
            semantic_text += f"\nOCR:\n{analysis.ocr_text}"
        semantic_text = truncate_utf8(semantic_text, 60_000)
        try:
            embeddings = await self._embedding_model.embed((semantic_text,))
        except EmbeddingError as error:
            error_type = (
                RetryableIngestError if error.retryable else PermanentIngestError
            )
            raise error_type("EMBEDDING_FAILED", str(error)) from error
        if len(embeddings) != 1:
            raise PermanentIngestError(
                "EMBEDDING_COUNT_MISMATCH",
                "embedding response count does not match the image unit",
            )

        command = IndexAssetCommand(
            request_id=str(event.event_id),
            tenant_id=event.tenant_id,
            acl_id=acl_id,
            asset_id=str(event.asset_id),
            asset_version_id=str(event.asset_version_id),
            asset_version=event.version_number,
            object_key=event.object_key,
            units=(
                IndexUnit(
                    unit_id=str(event.asset_version_id),
                    modality=Modality.IMAGE,
                    content=semantic_text,
                    title=truncate_utf8(analysis.caption, 2_048),
                    ordinal=0,
                    page_number=0,
                    content_sha256=event.content_sha256,
                    dense_embedding=embeddings[0],
                    embedding_model_id=self._embedding_model.model_id,
                    embedding_model_version=self._embedding_model.model_version,
                    metadata=(
                        ("media_type", image.media_type),
                        ("width", str(image.width)),
                        ("height", str(image.height)),
                        ("model_width", str(image.model_width)),
                        ("model_height", str(image.model_height)),
                        ("ocr_text", analysis.ocr_text),
                        ("vision_model_id", self._vision_model.model_id),
                        ("vision_model_version", self._vision_model.model_version),
                    ),
                ),
            ),
        )
        try:
            result = await self._core_client.index_asset(command)
        except CoreUnavailableError as error:
            raise RetryableIngestError("INDEX_CORE_UNAVAILABLE", str(error)) from error
        except ValueError as error:
            raise PermanentIngestError("INDEX_CONTRACT_INVALID", str(error)) from error
        if result.indexed_unit_count != 1:
            raise RetryableIngestError(
                "INDEX_COUNT_MISMATCH", "C++ Core indexed an unexpected image count"
            )
