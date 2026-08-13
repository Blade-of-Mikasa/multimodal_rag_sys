"""Application-owned upload types, ports, and expected failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class UploadError(RuntimeError):
    """Base class for expected upload workflow failures."""


class UploadNotFoundError(UploadError):
    pass


class UploadNotReadyError(UploadError):
    pass


class UploadTooLargeError(UploadError):
    pass


class UnsupportedMediaTypeError(UploadError):
    pass


class UploadValidationError(UploadError):
    def __init__(self, issues: dict[str, dict[str, object]]) -> None:
        super().__init__("uploaded object failed validation")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class PendingUpload:
    asset_id: str
    asset_version_id: str
    version_number: int
    object_key: str
    file_name: str
    content_type: str
    size_bytes: int
    content_sha256: str
    status: str
    ingest_task_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompletedUpload:
    asset_id: str
    asset_version_id: str
    version_number: int
    asset_status: str
    ingest_status: str
    ingest_task_id: str


@dataclass(frozen=True, slots=True)
class InitiatedUpload:
    asset_id: str
    asset_version_id: str
    version_number: int
    method: str
    upload_url: str
    required_headers: dict[str, str]
    expires_at: datetime


class UploadRepository(Protocol):
    async def create_pending_upload(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        asset_version_id: str,
        object_key: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
        content_sha256: str,
    ) -> None: ...

    async def find_upload(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
    ) -> PendingUpload | None: ...

    async def complete_upload(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
        storage_attributes: dict[str, Any],
    ) -> CompletedUpload: ...

    async def mark_upload_failed(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
        issues: dict[str, dict[str, object]],
    ) -> None: ...
