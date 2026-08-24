from __future__ import annotations

from hashlib import sha256
import unittest
from uuid import UUID

from rag_api.core_client import IndexAssetResult
from rag_api.documents.chunking import DocumentChunker
from rag_api.documents.parsers import DocumentParser
from rag_api.documents.processor import DocumentIngestProcessor
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import PermanentIngestError


PAYLOAD = b"# Architecture\n\nPython surface and C++ Milvus core."


def event(*, checksum: str | None = None) -> IngestTaskEvent:
    return IngestTaskEvent(
        event_id=UUID("10000000-0000-4000-8000-000000000001"),
        event_type="ingest.requested",
        task_id=UUID("20000000-0000-4000-8000-000000000001"),
        dedupe_key="asset-version-1:ingest",
        tenant_id="tenant-1",
        asset_id=UUID("30000000-0000-4000-8000-000000000001"),
        asset_version_id=UUID("40000000-0000-4000-8000-000000000001"),
        version_number=1,
        object_key="tenant-1/asset-1/v1/document.md",
        content_type="text/markdown",
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
        return "acl-1"


class FakeEmbeddingModel:
    model_id = "embedding-general"
    model_version = "v1"
    dimension = 2

    async def embed(self, texts):
        self.texts = tuple(texts)
        return tuple((1.0, 0.0) for _ in texts)


class FakeCoreClient:
    async def index_asset(self, command):
        self.command = command
        return IndexAssetResult(
            request_id=command.request_id,
            asset_id=command.asset_id,
            asset_version=command.asset_version,
            indexed_unit_count=len(command.units),
            collection_alias="rag_document_v1_test_2",
        )


class DocumentProcessorTest(unittest.IsolatedAsyncioTestCase):
    def processor(self) -> tuple[DocumentIngestProcessor, FakeCoreClient]:
        core = FakeCoreClient()
        return (
            DocumentIngestProcessor(
                object_store=FakeObjectStore(),
                parser=DocumentParser(),
                chunker=DocumentChunker(max_chars=128, overlap_chars=16),
                embedding_model=FakeEmbeddingModel(),
                core_client=core,
                acl_resolver=FakeAclResolver(),
                max_download_bytes=1_000_000,
                embedding_batch_size=2,
            ),
            core,
        )

    async def test_process_builds_complete_cpp_index_command(self) -> None:
        processor, core = self.processor()

        await processor.process(event())

        command = core.command
        self.assertEqual("tenant-1", command.tenant_id)
        self.assertEqual("acl-1", command.acl_id)
        self.assertEqual(1, len(command.units))
        self.assertEqual((1.0, 0.0), command.units[0].dense_embedding)
        self.assertEqual("embedding-general", command.units[0].embedding_model_id)
        self.assertEqual(64, len(command.units[0].content_sha256))

    async def test_checksum_mismatch_is_permanent(self) -> None:
        processor, _ = self.processor()

        with self.assertRaises(PermanentIngestError) as raised:
            await processor.process(event(checksum="0" * 64))

        self.assertEqual("DOCUMENT_CHECKSUM_MISMATCH", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
