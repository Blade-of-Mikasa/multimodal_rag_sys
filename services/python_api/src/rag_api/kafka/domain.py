"""Ports and domain values for the Kafka ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from rag_api.kafka.contracts import IngestTaskEvent


class KafkaIngestionError(RuntimeError):
    """Base class for expected pipeline failures."""


class LeaseLostError(KafkaIngestionError):
    pass


class RetryableIngestError(KafkaIngestionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PermanentIngestError(KafkaIngestionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class KafkaRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes
    headers: tuple[tuple[str, bytes | None], ...] = ()


@dataclass(frozen=True, slots=True)
class PublishedRecord:
    topic: str
    partition: int
    offset: int


class KafkaProducer(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: tuple[tuple[str, bytes], ...] = (),
    ) -> PublishedRecord: ...


class KafkaConsumer(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def getone(self) -> KafkaRecord: ...

    async def commit(self, record: KafkaRecord) -> None: ...

    async def rewind(self, record: KafkaRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxTask:
    task_id: str
    status: str
    attempt_count: int
    max_attempts: int
    dedupe_key: str
    tenant_id: str
    asset_id: str
    asset_version_id: str
    version_number: int
    object_key: str
    content_type: str
    size_bytes: int
    content_sha256: str
    error_code: str | None
    error_message: str | None


class ClaimDisposition(str, Enum):
    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    BUSY = "busy"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TaskClaim:
    disposition: ClaimDisposition
    task_id: str | None = None
    lease_owner: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FailureTransition:
    status: str
    available_at: datetime


class OutboxRepository(Protocol):
    async def claim_batch(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
    ) -> list[OutboxTask]: ...

    async def mark_published(
        self,
        *,
        task_id: str,
        owner: str,
        record: PublishedRecord,
    ) -> None: ...

    async def release_after_error(
        self,
        *,
        task_id: str,
        owner: str,
        message: str,
        retry_after_seconds: int,
    ) -> None: ...


class TaskRepository(Protocol):
    async def claim_for_processing(
        self,
        *,
        event: IngestTaskEvent,
        owner: str,
        lease_seconds: int,
    ) -> TaskClaim: ...

    async def complete_success(self, *, task_id: str, owner: str) -> None: ...

    async def renew_processing_lease(
        self,
        *,
        task_id: str,
        owner: str,
        lease_seconds: int,
    ) -> None: ...

    async def complete_failure(
        self,
        *,
        task_id: str,
        owner: str,
        code: str,
        message: str,
        retryable: bool,
        retry_base_seconds: int,
        retry_max_seconds: int,
    ) -> FailureTransition: ...


class IngestProcessor(Protocol):
    async def process(self, event: IngestTaskEvent) -> None: ...
