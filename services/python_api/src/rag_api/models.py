"""HTTP request and response models owned by the Python surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    service: str
    version: str
    environment: str
    status: Literal["ok", "degraded"] = "ok"
    ready: bool
    request_id: str
    checks: dict[str, Literal["ok", "unavailable"]] = Field(
        default_factory=dict
    )


class StreamQueryRequest(ApiModel):
    query: str = Field(min_length=1, max_length=8_000)
    conversation_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    retrieval_scope: Literal["auto", "local", "web", "hybrid"] = "auto"
    modalities: list[Literal["document", "image", "video"]] = Field(
        default_factory=lambda: ["document", "image", "video"],
        min_length=1,
        max_length=3,
    )

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("modalities")
    @classmethod
    def reject_duplicate_modalities(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("modalities must be unique")
        return value


class StreamEvent(ApiModel):
    event: Literal[
        "accepted",
        "planning",
        "retrieving",
        "sources",
        "delta",
        "heartbeat",
        "done",
        "error",
    ]
    request_id: str
    sequence: int = Field(ge=0)
    data: dict[str, Any]


class InitiateAssetUploadRequest(ApiModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(gt=0, le=5_000_000_000)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("file_name must not be blank")
        if any(ord(character) < 32 for character in value):
            raise ValueError("file_name must not contain control characters")
        return value

    @field_validator("content_type")
    @classmethod
    def normalize_content_type(cls, value: str) -> str:
        value = value.split(";", 1)[0].strip().lower()
        if "/" not in value:
            raise ValueError("content_type must be a media type")
        return value

    @field_validator("content_sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()


class InitiateAssetUploadResponse(ApiModel):
    request_id: str
    asset_id: str
    asset_version_id: str
    version_number: int
    method: Literal["PUT"]
    upload_url: str
    required_headers: dict[str, str]
    expires_at: datetime


class CompleteAssetUploadResponse(ApiModel):
    request_id: str
    asset_id: str
    asset_version_id: str
    version_number: int
    asset_status: Literal["processing", "ready"]
    ingest_status: Literal["processing", "ready"]
    ingest_task_id: str
    task_status: Literal["queued"] = "queued"
