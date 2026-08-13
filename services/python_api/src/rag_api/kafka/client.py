"""aiokafka adapters with reliability options fixed by construction."""

from __future__ import annotations

from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition

from rag_api.config import Settings
from rag_api.kafka.domain import KafkaRecord, PublishedRecord


def producer_options(settings: Settings) -> dict[str, Any]:
    """Build producer options without allowing reliability flags to drift."""

    options: dict[str, Any] = {
        "bootstrap_servers": settings.kafka_bootstrap_server_list,
        "client_id": f"{settings.kafka_client_id}-producer",
        "acks": "all",
        "enable_idempotence": True,
        "security_protocol": settings.kafka_security_protocol,
        "request_timeout_ms": 30_000,
        "retry_backoff_ms": 100,
    }
    _add_sasl_options(options, settings)
    return options


def consumer_options(settings: Settings) -> dict[str, Any]:
    """Build manual-commit consumer options for at-least-once delivery."""

    options: dict[str, Any] = {
        "bootstrap_servers": settings.kafka_bootstrap_server_list,
        "client_id": f"{settings.kafka_client_id}-consumer",
        "group_id": settings.kafka_consumer_group,
        "enable_auto_commit": False,
        "auto_offset_reset": "earliest",
        "isolation_level": "read_committed",
        "security_protocol": settings.kafka_security_protocol,
        "max_poll_interval_ms": 900_000,
    }
    _add_sasl_options(options, settings)
    return options


def _add_sasl_options(options: dict[str, Any], settings: Settings) -> None:
    if not settings.kafka_security_protocol.startswith("SASL_"):
        return
    assert settings.kafka_sasl_username is not None
    assert settings.kafka_sasl_password is not None
    options.update(
        sasl_mechanism=settings.kafka_sasl_mechanism,
        sasl_plain_username=settings.kafka_sasl_username.get_secret_value(),
        sasl_plain_password=settings.kafka_sasl_password.get_secret_value(),
    )


class AioKafkaProducerAdapter:
    """Small application-facing wrapper around ``AIOKafkaProducer``."""

    def __init__(self, settings: Settings) -> None:
        self._producer = AIOKafkaProducer(**producer_options(settings))

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: tuple[tuple[str, bytes], ...] = (),
    ) -> PublishedRecord:
        metadata = await self._producer.send_and_wait(
            topic,
            key=key,
            value=value,
            headers=list(headers),
        )
        return PublishedRecord(
            topic=metadata.topic,
            partition=metadata.partition,
            offset=metadata.offset,
        )


class AioKafkaConsumerAdapter:
    """Manual-commit consumer that exposes only safe operations."""

    def __init__(self, settings: Settings) -> None:
        self._consumer = AIOKafkaConsumer(
            settings.kafka_ingest_topic,
            settings.kafka_retry_topic,
            **consumer_options(settings),
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def getone(self) -> KafkaRecord:
        record = await self._consumer.getone()
        return KafkaRecord(
            topic=record.topic,
            partition=record.partition,
            offset=record.offset,
            key=record.key,
            value=record.value,
            headers=tuple(record.headers or ()),
        )

    async def commit(self, record: KafkaRecord) -> None:
        partition = TopicPartition(record.topic, record.partition)
        await self._consumer.commit({partition: record.offset + 1})

    async def rewind(self, record: KafkaRecord) -> None:
        partition = TopicPartition(record.topic, record.partition)
        self._consumer.seek(partition, record.offset)
