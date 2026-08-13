"""Versioned JSON contracts exchanged through Kafka."""

from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_DLQ_KEY_BYTES = 4_096
MAX_DLQ_VALUE_BYTES = 65_536
KafkaEventType = Literal[
    "ingest.requested",
    "ingest.retry",
    "ingest.dead_lettered",
]


class EventError(BaseModel):
    """Stable machine code plus bounded diagnostic context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Annotated[str, Field(min_length=1, max_length=64)]
    message: Annotated[str, Field(min_length=1, max_length=2000)]


class IngestTaskEvent(BaseModel):
    """Immutable command used to start or retry one ingestion task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    event_id: UUID = Field(default_factory=uuid4)
    event_type: KafkaEventType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    task_id: UUID
    dedupe_key: Annotated[str, Field(min_length=1, max_length=191)]
    tenant_id: Annotated[str, Field(min_length=1, max_length=64)]
    asset_id: UUID
    asset_version_id: UUID
    version_number: Annotated[int, Field(gt=0)]
    object_key: Annotated[str, Field(min_length=1, max_length=1024)]
    content_type: Annotated[str, Field(min_length=1, max_length=127)]
    size_bytes: Annotated[int, Field(ge=0)]
    content_sha256: Annotated[
        str,
        Field(pattern=r"^[0-9a-f]{64}$"),
    ]
    attempt: Annotated[int, Field(ge=0)]
    max_attempts: Annotated[int, Field(gt=0)]
    error: EventError | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)

    def encode(self) -> bytes:
        return self.model_dump_json(exclude_none=True).encode("utf-8")

    @classmethod
    def decode(cls, payload: bytes) -> "IngestTaskEvent":
        return cls.model_validate_json(payload)


class DeadLetterSource(BaseModel):
    """Coordinates of the Kafka record that could not be processed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: Annotated[str, Field(min_length=1, max_length=249)]
    partition: Annotated[int, Field(ge=0)]
    offset: Annotated[int, Field(ge=0)]


class DeadLetterEvent(BaseModel):
    """Deterministic DLQ envelope for malformed or rejected records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    event_id: UUID
    event_type: Literal["ingest.poisoned"] = "ingest.poisoned"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: DeadLetterSource
    error: EventError
    original_key_base64: str | None = None
    original_value_base64: str
    original_value_size: Annotated[int, Field(ge=0)]
    original_value_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    original_value_truncated: bool

    @classmethod
    def from_record(
        cls,
        *,
        namespace: UUID,
        topic: str,
        partition: int,
        offset: int,
        key: bytes | None,
        value: bytes,
        error_code: str,
        error_message: str,
    ) -> "DeadLetterEvent":
        source_key = f"{topic}:{partition}:{offset}"
        key_sample = key[:MAX_DLQ_KEY_BYTES] if key else None
        value_sample = value[:MAX_DLQ_VALUE_BYTES]
        return cls(
            event_id=uuid5(namespace, source_key),
            source=DeadLetterSource(
                topic=topic,
                partition=partition,
                offset=offset,
            ),
            error=EventError(
                code=(error_code or "UNKNOWN")[:64],
                message=(error_message or "no diagnostic message")[:2000],
            ),
            original_key_base64=(
                b64encode(key_sample).decode("ascii") if key_sample else None
            ),
            original_value_base64=b64encode(value_sample).decode("ascii"),
            original_value_size=len(value),
            original_value_sha256=sha256(value).hexdigest(),
            original_value_truncated=len(value) > len(value_sample),
        )

    def encode(self) -> bytes:
        return self.model_dump_json(exclude_none=True).encode("utf-8")
