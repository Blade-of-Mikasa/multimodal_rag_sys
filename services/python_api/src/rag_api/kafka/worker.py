"""Idempotent ingestion consumer with retry and poison-message handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from pydantic import ValidationError

from rag_api.config import Settings
from rag_api.kafka.contracts import DeadLetterEvent, IngestTaskEvent
from rag_api.kafka.domain import (
    ClaimDisposition,
    IngestProcessor,
    KafkaConsumer,
    KafkaProducer,
    KafkaRecord,
    LeaseLostError,
    PermanentIngestError,
    RetryableIngestError,
    TaskRepository,
)


POISON_EVENT_NAMESPACE = UUID("f74f4ca0-0f84-4b21-a547-6054fc165a23")


class WorkerDisposition(str, Enum):
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    POISONED = "poisoned"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    disposition: WorkerDisposition
    task_id: str | None = None
    reason: str | None = None


class IngestWorker:
    """Process one Kafka record and commit only after a durable outcome."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: TaskRepository,
        processor: IngestProcessor,
        consumer: KafkaConsumer,
        dlq_producer: KafkaProducer,
        owner: str,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._processor = processor
        self._consumer = consumer
        self._dlq_producer = dlq_producer
        self._owner = owner

    async def handle(self, record: KafkaRecord) -> WorkerResult:
        try:
            event = IngestTaskEvent.decode(record.value)
        except (ValidationError, ValueError, UnicodeDecodeError) as error:
            await self._poison(
                record,
                code="INVALID_EVENT",
                message=f"{type(error).__name__}: {error}",
            )
            await self._consumer.commit(record)
            return WorkerResult(
                WorkerDisposition.POISONED,
                reason="event contract validation failed",
            )

        if event.event_type not in {"ingest.requested", "ingest.retry"}:
            await self._poison(
                record,
                code="UNEXPECTED_EVENT_TYPE",
                message=f"consumer cannot process {event.event_type}",
            )
            await self._consumer.commit(record)
            return WorkerResult(
                WorkerDisposition.POISONED,
                task_id=str(event.task_id),
                reason="event type is not processable",
            )

        claim = await self._repository.claim_for_processing(
            event=event,
            owner=self._owner,
            lease_seconds=self._settings.kafka_processing_lease_seconds,
        )
        if claim.disposition == ClaimDisposition.REJECTED:
            await self._poison(
                record,
                code="EVENT_REJECTED",
                message=claim.reason or "event did not match durable task state",
            )
            await self._consumer.commit(record)
            return WorkerResult(
                WorkerDisposition.POISONED,
                task_id=claim.task_id,
                reason=claim.reason,
            )
        if claim.disposition == ClaimDisposition.DUPLICATE:
            await self._consumer.commit(record)
            return WorkerResult(
                WorkerDisposition.DUPLICATE,
                task_id=claim.task_id,
                reason=claim.reason,
            )
        if claim.disposition == ClaimDisposition.BUSY:
            await self._consumer.rewind(record)
            return WorkerResult(
                WorkerDisposition.DEFERRED,
                task_id=claim.task_id,
                reason=claim.reason,
            )

        task_id = claim.task_id
        assert task_id is not None
        try:
            await self._process_with_heartbeat(task_id, event)
        except LeaseLostError:
            raise
        except PermanentIngestError as error:
            await self._repository.complete_failure(
                task_id=task_id,
                owner=self._owner,
                code=error.code,
                message=str(error),
                retryable=False,
                retry_base_seconds=self._settings.kafka_retry_base_seconds,
                retry_max_seconds=self._settings.kafka_retry_max_seconds,
            )
            await self._consumer.commit(record)
            return WorkerResult(WorkerDisposition.FAILED, task_id=task_id)
        except RetryableIngestError as error:
            transition = await self._repository.complete_failure(
                task_id=task_id,
                owner=self._owner,
                code=error.code,
                message=str(error),
                retryable=True,
                retry_base_seconds=self._settings.kafka_retry_base_seconds,
                retry_max_seconds=self._settings.kafka_retry_max_seconds,
            )
            await self._consumer.commit(record)
            disposition = (
                WorkerDisposition.RETRY_SCHEDULED
                if transition.status == "retry"
                else WorkerDisposition.FAILED
            )
            return WorkerResult(disposition, task_id=task_id)
        except Exception as error:
            transition = await self._repository.complete_failure(
                task_id=task_id,
                owner=self._owner,
                code="UNEXPECTED_PROCESSING_ERROR",
                message=f"{type(error).__name__}: {error}",
                retryable=True,
                retry_base_seconds=self._settings.kafka_retry_base_seconds,
                retry_max_seconds=self._settings.kafka_retry_max_seconds,
            )
            await self._consumer.commit(record)
            disposition = (
                WorkerDisposition.RETRY_SCHEDULED
                if transition.status == "retry"
                else WorkerDisposition.FAILED
            )
            return WorkerResult(disposition, task_id=task_id)

        await self._repository.complete_success(task_id=task_id, owner=self._owner)
        await self._consumer.commit(record)
        return WorkerResult(WorkerDisposition.SUCCEEDED, task_id=task_id)

    async def _process_with_heartbeat(
        self,
        task_id: str,
        event: IngestTaskEvent,
    ) -> None:
        processing = asyncio.create_task(self._processor.process(event))
        heartbeat = asyncio.create_task(self._heartbeat(task_id))
        try:
            done, _ = await asyncio.wait(
                {processing, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            processing.cancel()
            heartbeat.cancel()
            await asyncio.gather(processing, heartbeat, return_exceptions=True)
            raise
        if heartbeat in done:
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
            heartbeat.result()
            raise AssertionError("lease heartbeat exited without an error")
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await processing

    async def _heartbeat(self, task_id: str) -> None:
        lease_seconds = self._settings.kafka_processing_lease_seconds
        interval_seconds = max(lease_seconds / 3, 0.1)
        while True:
            await asyncio.sleep(interval_seconds)
            await self._repository.renew_processing_lease(
                task_id=task_id,
                owner=self._owner,
                lease_seconds=lease_seconds,
            )

    async def run_forever(self, *, deferred_seconds: float = 0.5) -> None:
        await self._dlq_producer.start()
        try:
            await self._consumer.start()
            try:
                while True:
                    record = await self._consumer.getone()
                    result = await self.handle(record)
                    if result.disposition == WorkerDisposition.DEFERRED:
                        await asyncio.sleep(deferred_seconds)
            finally:
                await self._consumer.stop()
        finally:
            await self._dlq_producer.stop()

    async def _poison(
        self,
        record: KafkaRecord,
        *,
        code: str,
        message: str,
    ) -> None:
        event = DeadLetterEvent.from_record(
            namespace=POISON_EVENT_NAMESPACE,
            topic=record.topic,
            partition=record.partition,
            offset=record.offset,
            key=record.key,
            value=record.value,
            error_code=code,
            error_message=message,
        )
        await self._dlq_producer.send(
            topic=self._settings.kafka_dlq_topic,
            key=str(event.event_id).encode("ascii"),
            value=event.encode(),
            headers=(
                ("content-type", b"application/json"),
                ("schema-version", b"1"),
                ("event-type", b"ingest.poisoned"),
            ),
        )
