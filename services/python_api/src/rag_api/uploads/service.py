"""Two-phase asset upload orchestration."""

from __future__ import annotations

import base64
import logging
from uuid import uuid4

from rag_api.config import Settings
from rag_api.storage import (
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    StoredObject,
)
from rag_api.uploads.domain import (
    CompletedUpload,
    InitiatedUpload,
    PendingUpload,
    UnsupportedMediaTypeError,
    UploadNotFoundError,
    UploadNotReadyError,
    UploadRepository,
    UploadTooLargeError,
    UploadValidationError,
)


logger = logging.getLogger(__name__)

ALLOWED_EXACT_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/msword",
        "application/pdf",
        "application/rtf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)
ALLOWED_MEDIA_PREFIXES = ("audio/", "image/", "video/")


class AssetUploadService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: UploadRepository,
        object_store: ObjectStore,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._object_store = object_store

    async def initiate(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
        content_sha256: str,
    ) -> InitiatedUpload:
        normalized_content_type = _normalize_content_type(content_type)
        if not _is_supported_media_type(normalized_content_type):
            raise UnsupportedMediaTypeError(normalized_content_type)
        if size_bytes > self._settings.upload_max_bytes:
            raise UploadTooLargeError(str(size_bytes))

        asset_id = str(uuid4())
        asset_version_id = str(uuid4())
        object_key = (
            f"tenants/{tenant_id}/assets/{asset_id}/versions/1/source"
        )
        checksum_base64 = _hex_sha256_to_base64(content_sha256)
        metadata = {
            "asset-id": asset_id,
            "asset-version-id": asset_version_id,
        }
        presigned = await self._object_store.presign_put(
            object_key=object_key,
            content_type=normalized_content_type,
            size_bytes=size_bytes,
            checksum_sha256_base64=checksum_base64,
            metadata=metadata,
        )
        await self._repository.create_pending_upload(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            object_key=object_key,
            file_name=file_name,
            content_type=normalized_content_type,
            size_bytes=size_bytes,
            content_sha256=content_sha256.lower(),
        )
        return InitiatedUpload(
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            version_number=1,
            method="PUT",
            upload_url=presigned.url,
            required_headers=presigned.required_headers,
            expires_at=presigned.expires_at,
        )

    async def complete(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
    ) -> CompletedUpload:
        upload = await self._repository.find_upload(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            version_number=version_number,
        )
        if upload is None:
            raise UploadNotFoundError(asset_id)
        if upload.status in {"processing", "ready"} and upload.ingest_task_id:
            return CompletedUpload(
                asset_id=upload.asset_id,
                asset_version_id=upload.asset_version_id,
                version_number=upload.version_number,
                asset_status=upload.status,
                ingest_status=upload.status,
                ingest_task_id=upload.ingest_task_id,
            )
        if upload.status != "pending":
            raise UploadNotReadyError(upload.status)

        try:
            stored = await self._object_store.head(upload.object_key)
        except ObjectNotFoundError as error:
            raise UploadNotReadyError("object_not_found") from error

        issues = _validate_stored_object(upload, stored)
        if issues:
            try:
                await self._object_store.delete(upload.object_key)
            except ObjectStoreError:
                logger.warning(
                    "Failed to remove invalid upload",
                    extra={"asset_id": upload.asset_id},
                    exc_info=True,
                )
            await self._repository.mark_upload_failed(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                asset_id=asset_id,
                version_number=version_number,
                issues=issues,
            )
            raise UploadValidationError(issues)

        return await self._repository.complete_upload(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            version_number=version_number,
            storage_attributes={
                "etag": stored.etag,
                "version_id": stored.version_id,
                "checksum_sha256_base64": stored.checksum_sha256_base64,
            },
        )


def _normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _is_supported_media_type(value: str) -> bool:
    return value in ALLOWED_EXACT_MEDIA_TYPES or value.startswith(
        ALLOWED_MEDIA_PREFIXES
    )


def _hex_sha256_to_base64(value: str) -> str:
    return base64.b64encode(bytes.fromhex(value)).decode("ascii")


def _validate_stored_object(
    upload: PendingUpload,
    stored: StoredObject,
) -> dict[str, dict[str, object]]:
    expected_checksum = _hex_sha256_to_base64(upload.content_sha256)
    expected = {
        "size_bytes": upload.size_bytes,
        "content_type": upload.content_type,
        "checksum_sha256_base64": expected_checksum,
        "asset_id": upload.asset_id,
        "asset_version_id": upload.asset_version_id,
    }
    actual = {
        "size_bytes": stored.size_bytes,
        "content_type": _normalize_content_type(stored.content_type),
        "checksum_sha256_base64": stored.checksum_sha256_base64,
        "asset_id": stored.metadata.get("asset-id"),
        "asset_version_id": stored.metadata.get("asset-version-id"),
    }
    return {
        name: {"expected": expected_value, "actual": actual[name]}
        for name, expected_value in expected.items()
        if actual[name] != expected_value
    }
