from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from rag_api.config import Settings
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import OutboxTask, PublishedRecord
from rag_api.kafka.outbox import OutboxPublisher


TASK_IDS = [f"00000000-0000-4000-8000-00000000001{index}" for index in range(3)]
VERSION_IDS = [
    f"00000000-0000-4000-8000-00000000002{index}" for index in range(3)
]


def outbox_task(index: int, status: str) -> OutboxTask:
    return OutboxTask(
        task_id=TASK_IDS[index],
        status=status,
        attempt_count=index,
        max_attempts=5,
        dedupe_key=f"index_asset:{VERSION_IDS[index]}",
        tenant_id="tenant-1",
        asset_id=f"00000000-0000-4000-8000-00000000003{index}",
        asset_version_id=VERSION_IDS[index],
        version_number=1,
        object_key=f"tenants/tenant-1/assets/{index}/source",
        content_type="application/pdf",
        size_bytes=123,
        content_sha256="ab" * 32,
        error_code="EXTRACT_FAILED" if status != "queued" else None,
        error_message="parser unavailable" if status != "queued" else None,
    )


class FakeOutboxRepository:
    def __init__(self, tasks: list[OutboxTask]) -> None:
        self.tasks = tasks
        self.marked: list[tuple[str, PublishedRecord]] = []
        self.released: list[tuple[str, str]] = []

    async def claim_batch(self, **kwargs: object) -> list[OutboxTask]:
        self.claim_kwargs = kwargs
        return self.tasks

    async def mark_published(
        self,
        *,
        task_id: str,
        owner: str,
        record: PublishedRecord,
    ) -> None:
        self.marked.append((task_id, record))

    async def release_after_error(
        self,
        *,
        task_id: str,
        owner: str,
        message: str,
        retry_after_seconds: int,
    ) -> None:
        self.released.append((task_id, message))


class FakeProducer:
    def __init__(self, fail_topic: str | None = None) -> None:
        self.fail_topic = fail_topic
        self.sent: list[dict[str, object]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, **kwargs: object) -> PublishedRecord:
        self.sent.append(kwargs)
        if kwargs["topic"] == self.fail_topic:
            raise RuntimeError("broker unavailable")
        return PublishedRecord(str(kwargs["topic"]), 1, len(self.sent) - 1)


class KafkaOutboxTest(unittest.TestCase):
    def test_routes_initial_retry_and_terminal_events_to_distinct_topics(self) -> None:
        tasks = [
            outbox_task(0, "queued"),
            outbox_task(1, "retry"),
            outbox_task(2, "failed"),
        ]
        repository = FakeOutboxRepository(tasks)
        producer = FakeProducer()
        publisher = OutboxPublisher(
            settings=Settings(_env_file=None),
            repository=repository,
            producer=producer,
            owner="publisher-1",
        )

        result = asyncio.run(publisher.publish_batch())

        self.assertEqual((3, 3, 0), (result.claimed, result.published, result.failed))
        self.assertEqual(
            ["rag.ingest.v1", "rag.ingest.retry.v1", "rag.ingest.dlq.v1"],
            [str(call["topic"]) for call in producer.sent],
        )
        events = [
            IngestTaskEvent.decode(call["value"])
            for call in producer.sent
        ]
        self.assertEqual(
            ["ingest.requested", "ingest.retry", "ingest.dead_lettered"],
            [event.event_type for event in events],
        )
        self.assertEqual(VERSION_IDS[0].encode("ascii"), producer.sent[0]["key"])
        self.assertEqual(3, len(repository.marked))

    def test_publish_error_releases_lease_without_marking_success(self) -> None:
        task = outbox_task(1, "retry")
        repository = FakeOutboxRepository([task])
        producer = FakeProducer(fail_topic="rag.ingest.retry.v1")
        publisher = OutboxPublisher(
            settings=Settings(_env_file=None),
            repository=repository,
            producer=producer,
            owner="publisher-1",
        )

        with patch("rag_api.kafka.outbox.logger.exception") as log_error:
            result = asyncio.run(publisher.publish_batch())

        self.assertEqual((1, 0, 1), (result.claimed, result.published, result.failed))
        self.assertEqual([], repository.marked)
        self.assertIn("broker unavailable", repository.released[0][1])
        log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
