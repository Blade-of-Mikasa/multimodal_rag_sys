from __future__ import annotations

import asyncio
import unittest

from sqlalchemy.dialects import mysql

from rag_api.db.models import (
    AccessControlEntry,
    AccessControlList,
    Asset,
    AssetVersion,
    IngestTask,
)
from rag_api.uploads.repository import SqlAlchemyUploadRepository


ASSET_ID = "00000000-0000-4000-8000-000000000001"
VERSION_ID = "00000000-0000-4000-8000-000000000002"
ACL_ID = "00000000-0000-4000-8000-000000000003"


class FakeResult:
    def __init__(self, row: object) -> None:
        self.row = row

    def one_or_none(self) -> object:
        return self.row


class FakeSession:
    def __init__(self, row: object | None = None) -> None:
        self.row = row
        self.added: list[object] = []
        self.statements: list[object] = []
        self.scalar_result: str | None = None

    def add_all(self, values: list[object]) -> None:
        self.added.extend(values)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def execute(self, statement: object) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.row)

    async def scalar(self, statement: object) -> str | None:
        self.statements.append(statement)
        return self.scalar_result


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.committed = False

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.committed = exc_type is None


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.contexts: list[FakeSessionContext] = []

    def begin(self) -> FakeSessionContext:
        context = FakeSessionContext(self.session)
        self.contexts.append(context)
        return context


class UploadRepositoryTest(unittest.TestCase):
    def test_create_pending_upload_links_acl_asset_and_version(self) -> None:
        session = FakeSession()
        factory = FakeSessionFactory(session)
        repository = SqlAlchemyUploadRepository(factory)

        asyncio.run(
            repository.create_pending_upload(
                tenant_id="tenant-1",
                owner_user_id="user-1",
                asset_id=ASSET_ID,
                asset_version_id=VERSION_ID,
                object_key="tenants/tenant-1/assets/a/versions/1/source",
                file_name="report.pdf",
                content_type="application/pdf",
                size_bytes=123,
                content_sha256="ab" * 32,
            )
        )

        acl = _only(session.added, AccessControlList)
        entry = _only(session.added, AccessControlEntry)
        asset = _only(session.added, Asset)
        version = _only(session.added, AssetVersion)
        self.assertEqual(acl.id, entry.acl_id)
        self.assertEqual(acl.id, asset.acl_id)
        self.assertEqual("admin", entry.permission)
        self.assertEqual(ASSET_ID, version.asset_id)
        self.assertEqual(1, asset.latest_version_number)
        self.assertEqual("pending", version.ingest_status)
        self.assertTrue(factory.contexts[0].committed)

    def test_complete_upload_locks_state_and_adds_one_deduplicated_task(self) -> None:
        asset, version = _asset_and_version()
        session = FakeSession((asset, version))
        factory = FakeSessionFactory(session)
        repository = SqlAlchemyUploadRepository(factory)

        completed = asyncio.run(
            repository.complete_upload(
                tenant_id="tenant-1",
                owner_user_id="user-1",
                asset_id=ASSET_ID,
                version_number=1,
                storage_attributes={"etag": "etag-1"},
            )
        )

        task = _only(session.added, IngestTask)
        lock_sql = str(
            session.statements[0].compile(dialect=mysql.dialect())
        ).upper()
        self.assertIn("FOR UPDATE", lock_sql)
        self.assertEqual(f"index_asset:{VERSION_ID}", task.dedupe_key)
        self.assertEqual("queued", task.status)
        self.assertEqual("processing", asset.status)
        self.assertEqual("processing", version.ingest_status)
        self.assertEqual("etag-1", version.attributes["storage"]["etag"])
        self.assertEqual(task.id, completed.ingest_task_id)
        self.assertTrue(factory.contexts[0].committed)

    def test_complete_upload_returns_existing_task_idempotently(self) -> None:
        asset, version = _asset_and_version()
        asset.status = "processing"
        version.ingest_status = "processing"
        session = FakeSession((asset, version))
        session.scalar_result = "existing-task"
        repository = SqlAlchemyUploadRepository(FakeSessionFactory(session))

        completed = asyncio.run(
            repository.complete_upload(
                tenant_id="tenant-1",
                owner_user_id="user-1",
                asset_id=ASSET_ID,
                version_number=1,
                storage_attributes={"etag": "ignored"},
            )
        )

        self.assertEqual("existing-task", completed.ingest_task_id)
        self.assertEqual([], session.added)


def _asset_and_version() -> tuple[Asset, AssetVersion]:
    asset = Asset(
        id=ASSET_ID,
        tenant_id="tenant-1",
        owner_user_id="user-1",
        acl_id=ACL_ID,
        name="report.pdf",
        media_type="application/pdf",
        status="pending",
        latest_version_number=1,
    )
    version = AssetVersion(
        id=VERSION_ID,
        asset_id=ASSET_ID,
        version_number=1,
        object_key="tenants/tenant-1/assets/a/versions/1/source",
        content_sha256="ab" * 32,
        size_bytes=123,
        media_type="application/pdf",
        ingest_status="pending",
    )
    return asset, version


def _only(values: list[object], expected_type: type) -> object:
    matches = [value for value in values if isinstance(value, expected_type)]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {expected_type.__name__}, got {len(matches)}"
        )
    return matches[0]


if __name__ == "__main__":
    unittest.main()
