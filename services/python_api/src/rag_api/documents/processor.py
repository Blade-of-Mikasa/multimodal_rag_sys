"""Kafka ingestion processor composing Python normalization with C++ indexing."""

from __future__ import annotations

from hashlib import sha256

from rag_api.core_client import (
    CoreClient,
    CoreUnavailableError,
    IndexAssetCommand,
    IndexUnit,
)
from rag_api.documents.chunking import DocumentChunker
from rag_api.documents.domain import (
    AssetAclResolver,
    AssetIdentityError,
    DocumentParseError,
    EmbeddingError,
    EmbeddingModel,
)
from rag_api.documents.parsers import DocumentParser
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import PermanentIngestError, RetryableIngestError
from rag_api.storage import (
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    ObjectTooLargeError,
)


class DocumentIngestProcessor:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        parser: DocumentParser,
        chunker: DocumentChunker,
        embedding_model: EmbeddingModel,
        core_client: CoreClient,
        acl_resolver: AssetAclResolver,
        max_download_bytes: int,
        embedding_batch_size: int,
    ) -> None:
        self._object_store = object_store
        self._parser = parser
        self._chunker = chunker
        self._embedding_model = embedding_model
        self._core_client = core_client
        self._acl_resolver = acl_resolver
        self._max_download_bytes = max_download_bytes
        self._embedding_batch_size = embedding_batch_size

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
                "DOCUMENT_TOO_LARGE", "document exceeds the ingestion limit"
            ) from error
        except ObjectNotFoundError as error:
            raise RetryableIngestError(
                "DOCUMENT_NOT_FOUND", "document object is not available"
            ) from error
        except ObjectStoreError as error:
            raise RetryableIngestError(
                "OBJECT_STORE_UNAVAILABLE", str(error)
            ) from error

        if len(payload) != event.size_bytes:
            raise PermanentIngestError(
                "DOCUMENT_SIZE_MISMATCH", "downloaded object size does not match event"
            )
        if sha256(payload).hexdigest() != event.content_sha256:
            raise PermanentIngestError(
                "DOCUMENT_CHECKSUM_MISMATCH",
                "downloaded object checksum does not match event",
            )

        try:
            blocks = self._parser.parse(payload, event.content_type)
            chunks = self._chunker.chunk(
                asset_version_id=str(event.asset_version_id), blocks=blocks
            )
        except (DocumentParseError, ValueError) as error:
            raise PermanentIngestError("DOCUMENT_NORMALIZATION_FAILED", str(error)) from error

        embeddings: list[tuple[float, ...]] = []
        try:
            for start in range(0, len(chunks), self._embedding_batch_size):
                batch = chunks[start : start + self._embedding_batch_size]
                embeddings.extend(
                    await self._embedding_model.embed(
                        tuple(chunk.content for chunk in batch)
                    )
                )
        except EmbeddingError as error:
            error_type = RetryableIngestError if error.retryable else PermanentIngestError
            raise error_type("EMBEDDING_FAILED", str(error)) from error
        if len(embeddings) != len(chunks):
            raise PermanentIngestError(
                "EMBEDDING_COUNT_MISMATCH",
                "embedding response count does not match chunks",
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
                    unit_id=chunk.chunk_id,
                    content=chunk.content,
                    title=chunk.title,
                    ordinal=chunk.ordinal,
                    page_number=chunk.page_number,
                    content_sha256=chunk.content_sha256,
                    dense_embedding=embedding,
                    embedding_model_id=self._embedding_model.model_id,
                    embedding_model_version=self._embedding_model.model_version,
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ),
        )
        try:
            result = await self._core_client.index_asset(command)
        except CoreUnavailableError as error:
            raise RetryableIngestError("INDEX_CORE_UNAVAILABLE", str(error)) from error
        except ValueError as error:
            raise PermanentIngestError("INDEX_CONTRACT_INVALID", str(error)) from error
        if result.indexed_unit_count != len(chunks):
            raise RetryableIngestError(
                "INDEX_COUNT_MISMATCH", "C++ Core indexed an unexpected unit count"
            )
