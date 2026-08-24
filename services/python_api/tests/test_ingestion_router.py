from __future__ import annotations

from hashlib import sha256
import unittest
from uuid import UUID

from rag_api.ingestion.router import RoutingIngestProcessor
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import PermanentIngestError


def event(content_type: str) -> IngestTaskEvent:
    return IngestTaskEvent(
        event_id=UUID("10000000-0000-4000-8000-000000000009"),
        event_type="ingest.requested",
        task_id=UUID("20000000-0000-4000-8000-000000000009"),
        dedupe_key="router-test",
        tenant_id="tenant-1",
        asset_id=UUID("30000000-0000-4000-8000-000000000009"),
        asset_version_id=UUID("40000000-0000-4000-8000-000000000009"),
        version_number=1,
        object_key="tenant-1/test",
        content_type=content_type,
        size_bytes=0,
        content_sha256=sha256(b"").hexdigest(),
        attempt=0,
        max_attempts=5,
    )


class RecordingProcessor:
    def __init__(self) -> None:
        self.events = []

    async def process(self, ingest_event: IngestTaskEvent) -> None:
        self.events.append(ingest_event)


class IngestionRouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_media_type_and_routes_within_one_consumer(self) -> None:
        image = RecordingProcessor()
        document = RecordingProcessor()
        router = RoutingIngestProcessor(
            {"image/png": image, "text/plain": document}
        )

        await router.process(event("Image/PNG; charset=binary"))
        await router.process(event("text/plain"))

        self.assertEqual(1, len(image.events))
        self.assertEqual(1, len(document.events))

    async def test_unsupported_media_type_is_a_permanent_error(self) -> None:
        router = RoutingIngestProcessor({"image/png": RecordingProcessor()})

        with self.assertRaises(PermanentIngestError) as raised:
            await router.process(event("video/mp4"))

        self.assertEqual("UNSUPPORTED_MEDIA_TYPE", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
