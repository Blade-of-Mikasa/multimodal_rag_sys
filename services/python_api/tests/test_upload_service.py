from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import unittest

from rag_api.config import Settings
from rag_api.storage import ObjectNotFoundError, PresignedUpload, StoredObject
from rag_api.uploads.domain import (
    CompletedUpload,
    PendingUpload,
    UnsupportedMediaTypeError,
    UploadNotReadyError,
    UploadTooLargeError,
    UploadValidationError,
)
from rag_api.uploads.service import AssetUploadService


SHA256_HEX = "ab" * 32
SHA256_BASE64 = base64.b64encode(bytes.fromhex(SHA256_HEX)).decode("ascii")


class FakeObjectStore:
    def __init__(self) -> None:
        self.presign_arguments: dict[str, object] | None = None
        self.head_result: StoredObject | Exception | None = None
        self.head_calls = 0
        self.deleted_keys: list[str] = []

    async def presign_put(self, **arguments) -> PresignedUpload:
        self.presign_arguments = arguments
        return PresignedUpload(
            url="https://storage.example/upload",
            required_headers={
                "Content-Type": str(arguments["content_type"]),
                "x-amz-checksum-sha256": str(
                    arguments["checksum_sha256_base64"]
                ),
            },
            expires_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )

    async def head(self, object_key: str) -> StoredObject:
        self.head_calls += 1
        if isinstance(self.head_result, Exception):
            raise self.head_result
        assert self.head_result is not None
        return self.head_result

    async def delete(self, object_key: str) -> None:
        self.deleted_keys.append(object_key)


class FakeUploadRepository:
    def __init__(self) -> None:
        self.pending: PendingUpload | None = None
        self.created: dict[str, object] | None = None
        self.failed_issues: dict[str, dict[str, object]] | None = None
        self.completed_attributes: dict[str, object] | None = None

    async def create_pending_upload(self, **arguments) -> None:
        self.created = arguments
        self.pending = PendingUpload(
            asset_id=str(arguments["asset_id"]),
            asset_version_id=str(arguments["asset_version_id"]),
            version_number=1,
            object_key=str(arguments["object_key"]),
            file_name=str(arguments["file_name"]),
            content_type=str(arguments["content_type"]),
            size_bytes=int(arguments["size_bytes"]),
            content_sha256=str(arguments["content_sha256"]),
            status="pending",
        )

    async def find_upload(self, **arguments) -> PendingUpload | None:
        return self.pending

    async def complete_upload(self, **arguments) -> CompletedUpload:
        self.completed_attributes = arguments["storage_attributes"]
        assert self.pending is not None
        return CompletedUpload(
            asset_id=self.pending.asset_id,
            asset_version_id=self.pending.asset_version_id,
            version_number=1,
            asset_status="processing",
            ingest_status="processing",
            ingest_task_id="task-1",
        )

    async def mark_upload_failed(self, **arguments) -> None:
        self.failed_issues = arguments["issues"]


class AssetUploadServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            environment="test",
            upload_max_bytes=1000,
            _env_file=None,
        )
        self.repository = FakeUploadRepository()
        self.object_store = FakeObjectStore()
        self.service = AssetUploadService(
            settings=self.settings,
            repository=self.repository,
            object_store=self.object_store,
        )

    def test_initiate_presigns_constraints_then_registers_asset(self) -> None:
        initiated = asyncio.run(
            self.service.initiate(
                tenant_id="tenant-1",
                owner_user_id="user-1",
                file_name="report.pdf",
                content_type="Application/PDF; charset=binary",
                size_bytes=321,
                content_sha256=SHA256_HEX.upper(),
            )
        )

        self.assertEqual("PUT", initiated.method)
        self.assertEqual(1, initiated.version_number)
        self.assertEqual(initiated.asset_id, self.repository.created["asset_id"])
        self.assertEqual(
            f"tenants/tenant-1/assets/{initiated.asset_id}/versions/1/source",
            self.repository.created["object_key"],
        )
        self.assertEqual(
            SHA256_BASE64,
            self.object_store.presign_arguments["checksum_sha256_base64"],
        )
        self.assertEqual("application/pdf", self.repository.created["content_type"])

    def test_initiate_rejects_unsupported_type_and_oversized_file(self) -> None:
        with self.assertRaises(UnsupportedMediaTypeError):
            asyncio.run(
                self.service.initiate(
                    tenant_id="tenant-1",
                    owner_user_id="user-1",
                    file_name="page.html",
                    content_type="text/html",
                    size_bytes=10,
                    content_sha256=SHA256_HEX,
                )
            )
        with self.assertRaises(UploadTooLargeError):
            asyncio.run(
                self.service.initiate(
                    tenant_id="tenant-1",
                    owner_user_id="user-1",
                    file_name="video.mp4",
                    content_type="video/mp4",
                    size_bytes=1001,
                    content_sha256=SHA256_HEX,
                )
            )

    def test_complete_validates_object_and_enqueues_ingestion(self) -> None:
        pending = self._pending_upload()
        self.object_store.head_result = StoredObject(
            size_bytes=pending.size_bytes,
            content_type="application/pdf; charset=binary",
            checksum_sha256_base64=SHA256_BASE64,
            etag="etag-1",
            version_id="storage-version-1",
            metadata={
                "asset-id": pending.asset_id,
                "asset-version-id": pending.asset_version_id,
            },
        )

        completed = asyncio.run(
            self.service.complete(
                tenant_id="tenant-1",
                owner_user_id="user-1",
                asset_id=pending.asset_id,
                version_number=1,
            )
        )

        self.assertEqual("task-1", completed.ingest_task_id)
        self.assertEqual(
            "etag-1", self.repository.completed_attributes["etag"]
        )
        self.assertEqual([], self.object_store.deleted_keys)

    def test_complete_rejects_and_removes_mismatched_object(self) -> None:
        pending = self._pending_upload()
        self.object_store.head_result = StoredObject(
            size_bytes=pending.size_bytes + 1,
            content_type="application/pdf",
            checksum_sha256_base64=None,
            etag="etag-invalid",
            version_id=None,
            metadata={
                "asset-id": pending.asset_id,
                "asset-version-id": "wrong-version",
            },
        )

        with self.assertRaises(UploadValidationError) as raised:
            asyncio.run(
                self.service.complete(
                    tenant_id="tenant-1",
                    owner_user_id="user-1",
                    asset_id=pending.asset_id,
                    version_number=1,
                )
            )

        self.assertEqual(
            {
                "size_bytes",
                "checksum_sha256_base64",
                "asset_version_id",
            },
            set(raised.exception.issues),
        )
        self.assertEqual([pending.object_key], self.object_store.deleted_keys)
        self.assertEqual(raised.exception.issues, self.repository.failed_issues)

    def test_complete_reports_missing_object_without_failing_upload(self) -> None:
        pending = self._pending_upload()
        self.object_store.head_result = ObjectNotFoundError(pending.object_key)

        with self.assertRaises(UploadNotReadyError):
            asyncio.run(
                self.service.complete(
                    tenant_id="tenant-1",
                    owner_user_id="user-1",
                    asset_id=pending.asset_id,
                    version_number=1,
                )
            )

        self.assertIsNone(self.repository.failed_issues)

    def test_complete_is_idempotent_after_task_creation(self) -> None:
        pending = self._pending_upload()
        self.repository.pending = PendingUpload(
            **{
                field: getattr(pending, field)
                for field in pending.__dataclass_fields__
                if field not in {"status", "ingest_task_id"}
            },
            status="processing",
            ingest_task_id="existing-task",
        )

        completed = asyncio.run(
            self.service.complete(
                tenant_id="tenant-1",
                owner_user_id="user-1",
                asset_id=pending.asset_id,
                version_number=1,
            )
        )

        self.assertEqual("existing-task", completed.ingest_task_id)
        self.assertEqual(0, self.object_store.head_calls)

    def _pending_upload(self) -> PendingUpload:
        pending = PendingUpload(
            asset_id="00000000-0000-4000-8000-000000000001",
            asset_version_id="00000000-0000-4000-8000-000000000002",
            version_number=1,
            object_key="tenants/tenant-1/assets/asset/versions/1/source",
            file_name="report.pdf",
            content_type="application/pdf",
            size_bytes=321,
            content_sha256=SHA256_HEX,
            status="pending",
        )
        self.repository.pending = pending
        return pending


if __name__ == "__main__":
    unittest.main()
