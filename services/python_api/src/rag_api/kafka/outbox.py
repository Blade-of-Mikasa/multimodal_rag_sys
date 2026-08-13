"""Transactional-outbox publisher for ingestion, retry, and DLQ events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from uuid import UUID

from rag_api.config import Settings
from rag_api.kafka.contracts import EventError, IngestTaskEvent
from rag_api.kafka.domain import KafkaProducer, OutboxRepository, OutboxTask


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublishBatchResult:
    claimed: int
    published: int
    failed: int


class OutboxPublisher:
    """Publish leased database rows and store broker coordinates after ACK."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: OutboxRepository,
        producer: KafkaProducer,
        owner: str,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._producer = producer
        self._owner = owner

    async def publish_batch(self) -> PublishBatchResult:
        tasks = await self._repository.claim_batch(
            owner=self._owner,
            limit=self._settings.kafka_outbox_batch_size,
            lease_seconds=self._settings.kafka_publish_lease_seconds,
        )
        published = 0
        failed = 0
        for task in tasks:
            try:
                topic = self._topic_for(task.status)
                event = _event_for(task)
                record = await self._producer.send(
                    topic=topic,
                    key=task.asset_version_id.encode("ascii"),
                    value=event.encode(),
                    headers=(
                        ("content-type", b"application/json"),
                        ("schema-version", b"1"),
                        ("event-type", event.event_type.encode("ascii")),
                    ),
                )
                await self._repository.mark_published(
                    task_id=task.task_id,
                    owner=self._owner,
                    record=record,
                )
                published += 1
            except Exception as error:
                failed += 1
                logger.exception("Kafka publish failed for task %s", task.task_id)
                await self._repository.release_after_error(
                    task_id=task.task_id,
                    owner=self._owner,
                    message=f"{type(error).__name__}: {error}",
                    retry_after_seconds=self._settings.kafka_retry_base_seconds,
                )
        return PublishBatchResult(len(tasks), published, failed)

    async def run_forever(
        self,
        *,
        idle_seconds: float = 0.5,
    ) -> None:
        await self._producer.start()
        try:
            while True:
                result = await self.publish_batch()
                if result.claimed == 0:
                    await asyncio.sleep(idle_seconds)
        finally:
            await self._producer.stop()

    def _topic_for(self, status: str) -> str:
        if status == "queued":
            return self._settings.kafka_ingest_topic
        if status == "retry":
            return self._settings.kafka_retry_topic
        if status == "failed":
            return self._settings.kafka_dlq_topic
        raise ValueError(f"task status cannot be published: {status}")


def _event_for(task: OutboxTask) -> IngestTaskEvent:
    event_types = {
        "queued": "ingest.requested",
        "retry": "ingest.retry",
        "failed": "ingest.dead_lettered",
    }
    error = None
    if task.error_code or task.error_message:
        error = EventError(
            code=task.error_code or "UNKNOWN",
            message=task.error_message or "no diagnostic message",
        )
    return IngestTaskEvent(
        event_type=event_types[task.status],
        occurred_at=datetime.now(UTC),
        task_id=UUID(task.task_id),
        dedupe_key=task.dedupe_key,
        tenant_id=task.tenant_id,
        asset_id=UUID(task.asset_id),
        asset_version_id=UUID(task.asset_version_id),
        version_number=task.version_number,
        object_key=task.object_key,
        content_type=task.content_type,
        size_bytes=task.size_bytes,
        content_sha256=task.content_sha256,
        attempt=task.attempt_count,
        max_attempts=task.max_attempts,
        error=error,
    )
