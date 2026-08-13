from __future__ import annotations

import asyncio
from datetime import timedelta
import unittest

from sqlalchemy.dialects import mysql

from rag_api.db.models import Asset, AssetVersion, IngestTask
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import ClaimDisposition, PublishedRecord
from rag_api.kafka.repository import (
    SqlAlchemyKafkaRepository,
    retry_delay_seconds,
    utc_now,
)


TASK_ID = "00000000-0000-4000-8000-000000000010"
ASSET_ID = "00000000-0000-4000-8000-000000000011"
VERSION_ID = "00000000-0000-4000-8000-000000000012"
ACL_ID = "00000000-0000-4000-8000-000000000013"


class FakeResult:
    def __init__(self, row: object | None) -> None:
        self.row = row

    def all(self) -> list[object]:
        return [] if self.row is None else [self.row]

    def one_or_none(self) -> object | None:
        return self.row


class FakeSession:
    def __init__(self, row: object | None, task: IngestTask) -> None:
        self.row = row
        self.task = task
        self.statements: list[object] = []

    async def execute(self, statement: object) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.row)

    async def scalar(self, statement: object) -> IngestTask:
        self.statements.append(statement)
        return self.task


class FakeContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass


class FakeFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def begin(self) -> FakeContext:
        return FakeContext(self.session)


def task_graph() -> tuple[IngestTask, Asset, AssetVersion]:
    now = utc_now()
    asset = Asset(
        id=ASSET_ID,
        tenant_id="tenant-1",
        owner_user_id="user-1",
        acl_id=ACL_ID,
        name="report.pdf",
        media_type="application/pdf",
        status="processing",
        latest_version_number=1,
    )
    version = AssetVersion(
        id=VERSION_ID,
        asset_id=ASSET_ID,
        version_number=1,
        object_key="tenants/tenant-1/assets/a/source",
        content_sha256="ab" * 32,
        size_bytes=123,
        media_type="application/pdf",
        ingest_status="processing",
    )
    task = IngestTask(
        id=TASK_ID,
        asset_id=ASSET_ID,
        asset_version_id=VERSION_ID,
        task_type="index_asset",
        status="queued",
        attempt_count=0,
        max_attempts=5,
        dedupe_key=f"index_asset:{VERSION_ID}",
        available_at=now - timedelta(seconds=1),
        published_at=now,
        lease_owner=None,
        lease_expires_at=None,
        last_event_id=None,
    )
    return task, asset, version


def event(task: IngestTask, asset: Asset, version: AssetVersion) -> IngestTaskEvent:
    return IngestTaskEvent(
        event_type="ingest.requested",
        task_id=task.id,
        dedupe_key=task.dedupe_key,
        tenant_id=asset.tenant_id,
        asset_id=asset.id,
        asset_version_id=version.id,
        version_number=version.version_number,
        object_key=version.object_key,
        content_type=version.media_type,
        size_bytes=version.size_bytes,
        content_sha256=version.content_sha256,
        attempt=task.attempt_count,
        max_attempts=task.max_attempts,
    )


class KafkaRepositoryTest(unittest.TestCase):
    def test_outbox_claim_uses_skip_locked_and_records_broker_ack(self) -> None:
        task, asset, version = task_graph()
        task.published_at = None
        session = FakeSession((task, asset, version), task)
        repository = SqlAlchemyKafkaRepository(FakeFactory(session))

        claimed = asyncio.run(
            repository.claim_batch(owner="publisher-1", limit=10, lease_seconds=30)
        )

        sql = str(session.statements[0].compile(dialect=mysql.dialect())).upper()
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertEqual([TASK_ID], [item.task_id for item in claimed])
        self.assertEqual("publisher-1", task.lease_owner)

        asyncio.run(
            repository.mark_published(
                task_id=TASK_ID,
                owner="publisher-1",
                record=PublishedRecord("rag.ingest.v1", 2, 42),
            )
        )
        self.assertEqual(("rag.ingest.v1", 2, 42), (
            task.kafka_topic,
            task.kafka_partition,
            task.kafka_offset,
        ))
        self.assertIsNotNone(task.published_at)
        self.assertIsNone(task.lease_owner)
        self.assertIsNone(task.last_publish_error_message)

    def test_retry_transition_is_durable_and_uses_bounded_backoff(self) -> None:
        task, asset, version = task_graph()
        session = FakeSession((task, asset, version), task)
        repository = SqlAlchemyKafkaRepository(FakeFactory(session))

        claim = asyncio.run(
            repository.claim_for_processing(
                event=event(task, asset, version),
                owner="worker-1",
                lease_seconds=300,
            )
        )
        transition = asyncio.run(
            repository.complete_failure(
                task_id=TASK_ID,
                owner="worker-1",
                code="MODEL_BUSY",
                message="try later",
                retryable=True,
                retry_base_seconds=5,
                retry_max_seconds=900,
            )
        )

        self.assertEqual(ClaimDisposition.CLAIMED, claim.disposition)
        self.assertEqual(1, task.attempt_count)
        self.assertEqual("retry", transition.status)
        self.assertIsNone(task.published_at)
        self.assertEqual("processing", asset.status)
        self.assertEqual(900, retry_delay_seconds(
            attempt_count=40,
            base_seconds=5,
            maximum_seconds=900,
        ))

    def test_publish_error_does_not_overwrite_processing_error(self) -> None:
        task, asset, version = task_graph()
        task.status = "retry"
        task.published_at = None
        task.lease_owner = "publisher-1"
        task.last_error_code = "MODEL_BUSY"
        task.last_error_message = "try later"
        session = FakeSession((task, asset, version), task)
        repository = SqlAlchemyKafkaRepository(FakeFactory(session))

        asyncio.run(
            repository.release_after_error(
                task_id=TASK_ID,
                owner="publisher-1",
                message="broker unavailable",
                retry_after_seconds=5,
            )
        )

        self.assertEqual("MODEL_BUSY", task.last_error_code)
        self.assertEqual("try later", task.last_error_message)
        self.assertEqual("broker unavailable", task.last_publish_error_message)

    def test_permanent_failure_waits_for_dlq_then_becomes_dead_letter(self) -> None:
        task, asset, version = task_graph()
        session = FakeSession((task, asset, version), task)
        repository = SqlAlchemyKafkaRepository(FakeFactory(session))
        claim_event = event(task, asset, version)
        asyncio.run(
            repository.claim_for_processing(
                event=claim_event,
                owner="worker-1",
                lease_seconds=300,
            )
        )

        asyncio.run(
            repository.complete_failure(
                task_id=TASK_ID,
                owner="worker-1",
                code="BAD_FILE",
                message="unsupported",
                retryable=False,
                retry_base_seconds=5,
                retry_max_seconds=900,
            )
        )

        self.assertEqual("failed", task.status)
        self.assertEqual("failed", asset.status)
        self.assertEqual("failed", version.ingest_status)
        task.lease_owner = "publisher-1"
        asyncio.run(
            repository.mark_published(
                task_id=TASK_ID,
                owner="publisher-1",
                record=PublishedRecord("rag.ingest.dlq.v1", 1, 9),
            )
        )
        self.assertEqual("dead_letter", task.status)

    def test_success_updates_task_asset_and_version_atomically(self) -> None:
        task, asset, version = task_graph()
        session = FakeSession((task, asset, version), task)
        repository = SqlAlchemyKafkaRepository(FakeFactory(session))
        asyncio.run(
            repository.claim_for_processing(
                event=event(task, asset, version),
                owner="worker-1",
                lease_seconds=300,
            )
        )

        asyncio.run(repository.complete_success(task_id=TASK_ID, owner="worker-1"))

        self.assertEqual("succeeded", task.status)
        self.assertEqual("ready", asset.status)
        self.assertEqual("ready", version.ingest_status)
        self.assertIsNotNone(task.finished_at)


if __name__ == "__main__":
    unittest.main()
