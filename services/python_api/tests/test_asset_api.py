from __future__ import annotations

from datetime import datetime, timezone
import unittest

from fastapi.testclient import TestClient

from rag_api.app import create_app
from rag_api.config import Settings
from rag_api.core_client import CoreHealth
from rag_api.uploads.domain import (
    CompletedUpload,
    InitiatedUpload,
    UploadNotReadyError,
    UploadValidationError,
)


ASSET_ID = "00000000-0000-4000-8000-000000000001"
VERSION_ID = "00000000-0000-4000-8000-000000000002"
IDENTITY_HEADERS = {
    "X-Tenant-ID": "tenant-1",
    "X-User-ID": "user-1",
    "X-Request-ID": "upload-request-1",
}


class FakeCoreClient:
    async def health(self) -> CoreHealth:
        return CoreHealth(service="core", version="test", ready=True)

    async def execute_plan(self, plan):
        raise NotImplementedError

    async def close(self) -> None:
        pass


class FakeUploadService:
    def __init__(self) -> None:
        self.initiate_arguments: dict[str, object] | None = None
        self.complete_arguments: dict[str, object] | None = None
        self.complete_error: Exception | None = None

    async def initiate(self, **arguments) -> InitiatedUpload:
        self.initiate_arguments = arguments
        return InitiatedUpload(
            asset_id=ASSET_ID,
            asset_version_id=VERSION_ID,
            version_number=1,
            method="PUT",
            upload_url="https://storage.example/upload",
            required_headers={"Content-Type": "application/pdf"},
            expires_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )

    async def complete(self, **arguments) -> CompletedUpload:
        self.complete_arguments = arguments
        if self.complete_error is not None:
            raise self.complete_error
        return CompletedUpload(
            asset_id=ASSET_ID,
            asset_version_id=VERSION_ID,
            version_number=1,
            asset_status="processing",
            ingest_status="processing",
            ingest_task_id="task-1",
        )


class AssetApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.upload_service = FakeUploadService()
        self.client = TestClient(
            create_app(
                Settings(environment="test", _env_file=None),
                FakeCoreClient(),
                self.upload_service,
            )
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_initiate_upload_returns_presigned_contract(self) -> None:
        response = self.client.post(
            "/api/v1/assets/uploads",
            headers=IDENTITY_HEADERS,
            json={
                "file_name": "report.pdf",
                "content_type": "Application/PDF",
                "size_bytes": 123,
                "content_sha256": "AB" * 32,
            },
        )

        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual("upload-request-1", body["request_id"])
        self.assertEqual(ASSET_ID, body["asset_id"])
        self.assertEqual("PUT", body["method"])
        self.assertEqual(
            "application/pdf",
            self.upload_service.initiate_arguments["content_type"],
        )
        self.assertEqual(
            "ab" * 32,
            self.upload_service.initiate_arguments["content_sha256"],
        )

    def test_upload_requires_trusted_identity_headers(self) -> None:
        response = self.client.post(
            "/api/v1/assets/uploads",
            json={
                "file_name": "report.pdf",
                "content_type": "application/pdf",
                "size_bytes": 123,
                "content_sha256": "ab" * 32,
            },
        )

        self.assertEqual(422, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.json()["error"]["code"])

    def test_unsafe_tenant_id_is_rejected_before_service(self) -> None:
        response = self.client.post(
            "/api/v1/assets/uploads",
            headers={**IDENTITY_HEADERS, "X-Tenant-ID": "../other"},
            json={
                "file_name": "report.pdf",
                "content_type": "application/pdf",
                "size_bytes": 123,
                "content_sha256": "ab" * 32,
            },
        )

        self.assertEqual(422, response.status_code)
        self.assertIsNone(self.upload_service.initiate_arguments)

    def test_complete_upload_returns_queued_task(self) -> None:
        response = self.client.post(
            f"/api/v1/assets/{ASSET_ID}/versions/1/complete",
            headers=IDENTITY_HEADERS,
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("task-1", response.json()["ingest_task_id"])
        self.assertEqual("queued", response.json()["task_status"])
        self.assertEqual(
            "tenant-1", self.upload_service.complete_arguments["tenant_id"]
        )

    def test_complete_reports_not_ready_with_stable_error(self) -> None:
        self.upload_service.complete_error = UploadNotReadyError(
            "object_not_found"
        )

        response = self.client.post(
            f"/api/v1/assets/{ASSET_ID}/versions/1/complete",
            headers=IDENTITY_HEADERS,
        )

        self.assertEqual(409, response.status_code)
        self.assertEqual("UPLOAD_NOT_READY", response.json()["error"]["code"])
        self.assertEqual(
            "object_not_found", response.json()["error"]["details"]["state"]
        )

    def test_complete_reports_integrity_mismatch_details(self) -> None:
        self.upload_service.complete_error = UploadValidationError(
            {"size_bytes": {"expected": 123, "actual": 122}}
        )

        response = self.client.post(
            f"/api/v1/assets/{ASSET_ID}/versions/1/complete",
            headers=IDENTITY_HEADERS,
        )

        self.assertEqual(409, response.status_code)
        self.assertEqual(
            "UPLOAD_VALIDATION_FAILED", response.json()["error"]["code"]
        )
        self.assertEqual(
            122,
            response.json()["error"]["details"]["size_bytes"]["actual"],
        )


if __name__ == "__main__":
    unittest.main()
