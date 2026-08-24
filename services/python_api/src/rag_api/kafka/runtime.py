"""Composition helpers for Kafka processes and future ingestion handlers."""

from __future__ import annotations

from uuid import uuid4

from rag_api.config import Settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_api.kafka.client import AioKafkaConsumerAdapter, AioKafkaProducerAdapter
from rag_api.kafka.domain import IngestProcessor
from rag_api.kafka.outbox import OutboxPublisher
from rag_api.kafka.repository import SqlAlchemyKafkaRepository
from rag_api.kafka.worker import IngestWorker


def create_outbox_publisher(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    owner: str | None = None,
) -> OutboxPublisher:
    """Compose the standalone database-to-Kafka outbox publisher."""

    repository = SqlAlchemyKafkaRepository(session_factory)
    return OutboxPublisher(
        settings=settings,
        repository=repository,
        producer=AioKafkaProducerAdapter(settings),
        owner=owner or f"outbox-{uuid4()}",
    )


def create_ingest_worker(
    settings: Settings,
    *,
    processor: IngestProcessor,
    session_factory: async_sessionmaker[AsyncSession],
    owner: str | None = None,
) -> IngestWorker:
    """Compose a consumer around the processor supplied by M07-M09."""

    repository = SqlAlchemyKafkaRepository(session_factory)
    return IngestWorker(
        settings=settings,
        repository=repository,
        processor=processor,
        consumer=AioKafkaConsumerAdapter(settings),
        dlq_producer=AioKafkaProducerAdapter(settings),
        owner=owner or f"ingest-{uuid4()}",
    )
