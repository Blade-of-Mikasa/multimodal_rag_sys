from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import unittest

from rag_api.config import Settings
from rag_api.kafka.contracts import DeadLetterEvent, IngestTaskEvent
from rag_api.kafka.domain import (
    ClaimDisposition,
    FailureTransition,
    KafkaRecord,
    LeaseLostError,
    PermanentIngestError,
    PublishedRecord,
    RetryableIngestError,
    TaskClaim,
)
from rag_api.kafka.worker import IngestWorker, WorkerDisposition


TASK_ID = "00000000-0000-4000-8000-000000000010"
ASSET_ID = "00000000-0000-4000-8000-000000000011"
VERSION_ID = "00000000-0000-4000-8000-000000000012"


def event_record(offset: int = 7) -> KafkaRecord:
    event = IngestTaskEvent(
        event_type="ingest.requested",
        occurred_at=datetime(2026, 8, 13, tzinfo=UTC),
        task_id=TASK_ID,
        dedupe_key=f"index_asset:{VERSION_ID}",
        tenant_id="tenant-1",
        asset_id=ASSET_ID,
        asset_version_id=VERSION_ID,
        version_number=1,
        object_key="tenants/tenant-1/assets/a/source",
        content_type="application/pdf",
        size_bytes=123,
        content_sha256="ab" * 32,
        attempt=0,
        max_attempts=5,
    )
    return KafkaRecord("rag.ingest.v1", 0, offset, VERSION_ID.encode(), event.encode())


class FakeRepository:
    def __init__(self, trace: list[str], disposition: ClaimDisposition) -> None:
        self.trace = trace
        self.disposition = disposition
        self.failure_status = "retry"
        self.renew_error: Exception | None = None

    async def claim_for_processing(self, **kwargs: object) -> TaskClaim:
        self.trace.append("db:claim")
        return TaskClaim(
            self.disposition,
            task_id=TASK_ID,
            lease_owner="worker-1",
            reason="test disposition",
        )

    async def complete_success(self, **kwargs: object) -> None:
        self.trace.append("db:success")

    async def renew_processing_lease(self, **kwargs: object) -> None:
        self.trace.append("db:renew")
        if self.renew_error:
            raise self.renew_error

    async def complete_failure(self, **kwargs: object) -> FailureTransition:
        self.trace.append(f"db:{self.failure_status}")
        return FailureTransition(
            self.failure_status,
            datetime(2026, 8, 13),
        )


class FakeConsumer:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def getone(self) -> KafkaRecord:
        raise AssertionError("not used")

    async def commit(self, record: KafkaRecord) -> None:
        self.trace.append(f"kafka:commit:{record.offset}")

    async def rewind(self, record: KafkaRecord) -> None:
        self.trace.append(f"kafka:rewind:{record.offset}")


class FakeProducer:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.sent: list[dict[str, object]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, **kwargs: object) -> PublishedRecord:
        self.trace.append("kafka:dlq")
        self.sent.append(kwargs)
        return PublishedRecord(str(kwargs["topic"]), 0, 1)


class FakeProcessor:
    def __init__(self, trace: list[str], error: Exception | None = None) -> None:
        self.trace = trace
        self.error = error

    async def process(self, event: IngestTaskEvent) -> None:
        self.trace.append("processor")
        if self.error:
            raise self.error


class SlowProcessor:
    def __init__(self, trace: list[str], delay: float) -> None:
        self.trace = trace
        self.delay = delay
        self.cancelled = False

    async def process(self, event: IngestTaskEvent) -> None:
        self.trace.append("processor")
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def worker(
    *,
    disposition: ClaimDisposition = ClaimDisposition.CLAIMED,
    processor_error: Exception | None = None,
) -> tuple[IngestWorker, FakeRepository, FakeProducer, list[str]]:
    trace: list[str] = []
    repository = FakeRepository(trace, disposition)
    producer = FakeProducer(trace)
    instance = IngestWorker(
        settings=Settings(_env_file=None),
        repository=repository,
        processor=FakeProcessor(trace, processor_error),
        consumer=FakeConsumer(trace),
        dlq_producer=producer,
        owner="worker-1",
    )
    return instance, repository, producer, trace


class KafkaWorkerTest(unittest.TestCase):
    def test_success_is_persisted_before_offset_commit(self) -> None:
        instance, _, _, trace = worker()

        result = asyncio.run(instance.handle(event_record()))

        self.assertEqual(WorkerDisposition.SUCCEEDED, result.disposition)
        self.assertEqual(
            ["db:claim", "processor", "db:success", "kafka:commit:7"],
            trace,
        )

    def test_retry_is_scheduled_before_offset_commit(self) -> None:
        instance, _, _, trace = worker(
            processor_error=RetryableIngestError("MODEL_BUSY", "try later")
        )

        result = asyncio.run(instance.handle(event_record()))

        self.assertEqual(WorkerDisposition.RETRY_SCHEDULED, result.disposition)
        self.assertEqual("db:retry", trace[-2])
        self.assertEqual("kafka:commit:7", trace[-1])

    def test_permanent_failure_is_persisted_before_offset_commit(self) -> None:
        instance, repository, _, trace = worker(
            processor_error=PermanentIngestError("BAD_FILE", "unsupported")
        )
        repository.failure_status = "failed"

        result = asyncio.run(instance.handle(event_record()))

        self.assertEqual(WorkerDisposition.FAILED, result.disposition)
        self.assertEqual("db:failed", trace[-2])
        self.assertEqual("kafka:commit:7", trace[-1])

    def test_busy_task_rewinds_without_committing(self) -> None:
        instance, _, _, trace = worker(disposition=ClaimDisposition.BUSY)

        result = asyncio.run(instance.handle(event_record()))

        self.assertEqual(WorkerDisposition.DEFERRED, result.disposition)
        self.assertEqual(["db:claim", "kafka:rewind:7"], trace)

    def test_duplicate_commits_without_running_processor(self) -> None:
        instance, _, _, trace = worker(disposition=ClaimDisposition.DUPLICATE)

        result = asyncio.run(instance.handle(event_record()))

        self.assertEqual(WorkerDisposition.DUPLICATE, result.disposition)
        self.assertEqual(["db:claim", "kafka:commit:7"], trace)

    def test_invalid_message_reaches_dlq_before_offset_commit(self) -> None:
        instance, _, producer, trace = worker()
        record = KafkaRecord("rag.ingest.v1", 3, 21, b"key", b"not-json")

        result = asyncio.run(instance.handle(record))

        self.assertEqual(WorkerDisposition.POISONED, result.disposition)
        self.assertEqual(["kafka:dlq", "kafka:commit:21"], trace)
        dlq_event = DeadLetterEvent.model_validate_json(producer.sent[0]["value"])
        self.assertEqual(3, dlq_event.source.partition)
        self.assertEqual(21, dlq_event.source.offset)

    def test_long_processing_renews_database_lease(self) -> None:
        trace: list[str] = []
        repository = FakeRepository(trace, ClaimDisposition.CLAIMED)
        instance = IngestWorker(
            settings=Settings(kafka_processing_lease_seconds=1, _env_file=None),
            repository=repository,
            processor=SlowProcessor(trace, 0.4),
            consumer=FakeConsumer(trace),
            dlq_producer=FakeProducer(trace),
            owner="worker-1",
        )

        result = asyncio.run(instance.handle(event_record()))

        self.assertEqual(WorkerDisposition.SUCCEEDED, result.disposition)
        self.assertIn("db:renew", trace)
        self.assertLess(trace.index("db:renew"), trace.index("db:success"))

    def test_lost_heartbeat_cancels_processor_without_committing(self) -> None:
        trace: list[str] = []
        repository = FakeRepository(trace, ClaimDisposition.CLAIMED)
        repository.renew_error = LeaseLostError("lease moved")
        processor = SlowProcessor(trace, 2)
        instance = IngestWorker(
            settings=Settings(kafka_processing_lease_seconds=1, _env_file=None),
            repository=repository,
            processor=processor,
            consumer=FakeConsumer(trace),
            dlq_producer=FakeProducer(trace),
            owner="worker-1",
        )

        with self.assertRaises(LeaseLostError):
            asyncio.run(instance.handle(event_record()))

        self.assertTrue(processor.cancelled)
        self.assertNotIn("kafka:commit:7", trace)


if __name__ == "__main__":
    unittest.main()
