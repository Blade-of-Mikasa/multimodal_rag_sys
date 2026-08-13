from __future__ import annotations

from base64 import b64decode
from datetime import UTC, datetime
import unittest
from uuid import UUID

from pydantic import ValidationError

from rag_api.kafka.contracts import (
    MAX_DLQ_VALUE_BYTES,
    DeadLetterEvent,
    IngestTaskEvent,
)
from rag_api.kafka.worker import POISON_EVENT_NAMESPACE


TASK_ID = UUID("00000000-0000-4000-8000-000000000010")
ASSET_ID = UUID("00000000-0000-4000-8000-000000000011")
VERSION_ID = UUID("00000000-0000-4000-8000-000000000012")


def ingest_event(**overrides: object) -> IngestTaskEvent:
    values: dict[str, object] = {
        "event_type": "ingest.requested",
        "occurred_at": datetime(2026, 8, 13, tzinfo=UTC),
        "task_id": TASK_ID,
        "dedupe_key": f"index_asset:{VERSION_ID}",
        "tenant_id": "tenant-1",
        "asset_id": ASSET_ID,
        "asset_version_id": VERSION_ID,
        "version_number": 1,
        "object_key": "tenants/tenant-1/assets/a/versions/1/source",
        "content_type": "application/pdf",
        "size_bytes": 123,
        "content_sha256": "ab" * 32,
        "attempt": 0,
        "max_attempts": 5,
    }
    values.update(overrides)
    return IngestTaskEvent(**values)


class KafkaContractTest(unittest.TestCase):
    def test_ingest_event_round_trips_as_versioned_json(self) -> None:
        event = ingest_event()

        decoded = IngestTaskEvent.decode(event.encode())

        self.assertEqual(event, decoded)
        self.assertEqual("1", decoded.schema_version)
        self.assertEqual(UTC, decoded.occurred_at.tzinfo)

    def test_ingest_event_rejects_unknown_fields_and_naive_time(self) -> None:
        payload = ingest_event().model_dump()
        payload["unexpected"] = True
        with self.assertRaises(ValidationError):
            IngestTaskEvent.model_validate(payload)
        with self.assertRaises(ValidationError):
            ingest_event(occurred_at=datetime(2026, 8, 13))

    def test_poison_event_is_deterministic_and_preserves_raw_bytes(self) -> None:
        first = DeadLetterEvent.from_record(
            namespace=POISON_EVENT_NAMESPACE,
            topic="rag.ingest.v1",
            partition=2,
            offset=99,
            key=b"asset-key",
            value=b"not-json\x00",
            error_code="INVALID_EVENT",
            error_message="bad payload",
        )
        second = DeadLetterEvent.from_record(
            namespace=POISON_EVENT_NAMESPACE,
            topic="rag.ingest.v1",
            partition=2,
            offset=99,
            key=b"asset-key",
            value=b"not-json\x00",
            error_code="INVALID_EVENT",
            error_message="bad payload",
        )

        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(b"asset-key", b64decode(first.original_key_base64))
        self.assertEqual(b"not-json\x00", b64decode(first.original_value_base64))
        self.assertFalse(first.original_value_truncated)

    def test_poison_event_bounds_oversized_payload_but_keeps_identity(self) -> None:
        payload = b"x" * (MAX_DLQ_VALUE_BYTES + 1)

        event = DeadLetterEvent.from_record(
            namespace=POISON_EVENT_NAMESPACE,
            topic="rag.ingest.v1",
            partition=0,
            offset=1,
            key=None,
            value=payload,
            error_code="INVALID_EVENT",
            error_message="oversized payload",
        )

        self.assertEqual(MAX_DLQ_VALUE_BYTES, len(b64decode(event.original_value_base64)))
        self.assertEqual(len(payload), event.original_value_size)
        self.assertTrue(event.original_value_truncated)


if __name__ == "__main__":
    unittest.main()
