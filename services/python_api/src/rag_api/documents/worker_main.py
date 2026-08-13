"""Standalone Kafka document worker process."""

from __future__ import annotations

import asyncio

from rag_api.config import Settings
from rag_api.core_client import GrpcCoreClient
from rag_api.db.session import create_database_engine, create_session_factory
from rag_api.documents.acl import SqlAlchemyAssetAclResolver
from rag_api.documents.chunking import DocumentChunker
from rag_api.documents.embeddings import HttpEmbeddingModel
from rag_api.documents.parsers import DocumentParser
from rag_api.documents.processor import DocumentIngestProcessor
from rag_api.kafka.runtime import create_ingest_worker
from rag_api.storage import S3ObjectStore


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
    api_key = (
        settings.embedding_api_key.get_secret_value()
        if settings.embedding_api_key is not None
        else None
    )
    processor = DocumentIngestProcessor(
        object_store=S3ObjectStore(settings),
        parser=DocumentParser(),
        chunker=DocumentChunker(
            max_chars=settings.document_chunk_max_chars,
            overlap_chars=settings.document_chunk_overlap_chars,
        ),
        embedding_model=HttpEmbeddingModel(
            endpoint_url=settings.embedding_endpoint_url,
            api_key=api_key,
            model_id=settings.embedding_model_id,
            model_version=settings.embedding_model_version,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
        ),
        core_client=core_client,
        acl_resolver=SqlAlchemyAssetAclResolver(session_factory),
        max_download_bytes=settings.document_download_max_bytes,
        embedding_batch_size=settings.embedding_batch_size,
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
