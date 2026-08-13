"""MySQL-backed outbox and idempotent consumer state transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_api.db.models import Asset, AssetVersion, IngestTask
from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import (
    ClaimDisposition,
    FailureTransition,
    LeaseLostError,
    OutboxTask,
    PublishedRecord,
    TaskClaim,
)


OUTBOX_STATUSES = ("queued", "retry", "failed")
TERMINAL_STATUSES = ("succeeded", "failed", "dead_letter")


def utc_now() -> datetime:
    """Return UTC in MySQL ``DATETIME`` form (timezone information removed)."""

    return datetime.now(UTC).replace(tzinfo=None)


def retry_delay_seconds(
    *,
    attempt_count: int,
    base_seconds: int,
    maximum_seconds: int,
) -> int:
    """Calculate bounded exponential backoff without unbounded exponentiation."""

    exponent = min(max(attempt_count - 1, 0), 30)
    return min(base_seconds * (2**exponent), maximum_seconds)


class SqlAlchemyKafkaRepository:
    """Coordinate the database side of the Kafka at-least-once protocol."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def claim_batch(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
    ) -> list[OutboxTask]:
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        async with self._session_factory.begin() as session:
            rows = (
                await session.execute(
                    select(IngestTask, Asset, AssetVersion)
                    .join(Asset, Asset.id == IngestTask.asset_id)
                    .join(
                        AssetVersion,
                        AssetVersion.id == IngestTask.asset_version_id,
                    )
                    .where(
                        IngestTask.published_at.is_(None),
                        IngestTask.status.in_(OUTBOX_STATUSES),
                        IngestTask.available_at <= now,
                        or_(
                            IngestTask.lease_expires_at.is_(None),
                            IngestTask.lease_expires_at <= now,
                        ),
                    )
                    .order_by(IngestTask.available_at, IngestTask.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            claimed: list[OutboxTask] = []
            for task, asset, version in rows:
                task.lease_owner = owner
                task.lease_expires_at = lease_expires_at
                claimed.append(_outbox_task(task, asset, version))
            return claimed

    async def mark_published(
        self,
        *,
        task_id: str,
        owner: str,
        record: PublishedRecord,
    ) -> None:
        now = utc_now()
        async with self._session_factory.begin() as session:
            task = await _locked_task(session, task_id)
            _require_lease(task, owner)
            task.kafka_topic = record.topic
            task.kafka_partition = record.partition
            task.kafka_offset = record.offset
            task.published_at = now
            task.last_publish_error_message = None
            task.lease_owner = None
            task.lease_expires_at = None
            if task.status == "failed":
                task.status = "dead_letter"
                task.finished_at = now

    async def release_after_error(
        self,
        *,
        task_id: str,
        owner: str,
        message: str,
        retry_after_seconds: int,
    ) -> None:
        now = utc_now()
        async with self._session_factory.begin() as session:
            task = await _locked_task(session, task_id)
            _require_lease(task, owner)
            task.lease_owner = None
            task.lease_expires_at = None
            task.available_at = now + timedelta(seconds=retry_after_seconds)
            task.last_publish_error_message = message[:2000]

    async def claim_for_processing(
        self,
        *,
        event: IngestTaskEvent,
        owner: str,
        lease_seconds: int,
    ) -> TaskClaim:
        now = utc_now()
        task_id = str(event.task_id)
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(IngestTask, Asset, AssetVersion)
                    .join(Asset, Asset.id == IngestTask.asset_id)
                    .join(
                        AssetVersion,
                        AssetVersion.id == IngestTask.asset_version_id,
                    )
                    .where(IngestTask.id == task_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return TaskClaim(
                    ClaimDisposition.REJECTED,
                    reason="task does not exist",
                )
            task, asset, version = row
            mismatch = _event_mismatch(event, task, asset, version)
            if mismatch:
                return TaskClaim(
                    ClaimDisposition.REJECTED,
                    task_id=task.id,
                    reason=mismatch,
                )
            if task.status in TERMINAL_STATUSES:
                return TaskClaim(
                    ClaimDisposition.DUPLICATE,
                    task_id=task.id,
                    reason=f"task is already {task.status}",
                )

            if task.status == "running":
                if task.lease_expires_at is not None and task.lease_expires_at > now:
                    return TaskClaim(
                        ClaimDisposition.BUSY,
                        task_id=task.id,
                        reason="processing lease is still active",
                    )
                if event.attempt != task.attempt_count - 1:
                    return TaskClaim(
                        ClaimDisposition.DUPLICATE,
                        task_id=task.id,
                        reason="event attempt is stale",
                    )
                if task.attempt_count >= task.max_attempts:
                    _mark_terminal_failure(
                        task,
                        asset,
                        version,
                        now=now,
                        code="PROCESSING_LEASE_EXPIRED",
                        message="processing lease expired at the attempt limit",
                    )
                    return TaskClaim(
                        ClaimDisposition.DUPLICATE,
                        task_id=task.id,
                        reason="expired task moved to failed",
                    )
            elif task.status in {"queued", "retry"}:
                expected_event_type = (
                    "ingest.requested" if task.status == "queued" else "ingest.retry"
                )
                if event.event_type != expected_event_type:
                    return TaskClaim(
                        ClaimDisposition.REJECTED,
                        task_id=task.id,
                        reason=(
                            f"{task.status} task requires {expected_event_type} event"
                        ),
                    )
                if event.attempt != task.attempt_count:
                    return TaskClaim(
                        ClaimDisposition.DUPLICATE,
                        task_id=task.id,
                        reason="event attempt is stale",
                    )
                if task.available_at > now:
                    return TaskClaim(
                        ClaimDisposition.BUSY,
                        task_id=task.id,
                        reason="retry is not due yet",
                    )
                if (
                    task.published_at is None
                    and task.lease_expires_at is not None
                    and task.lease_expires_at > now
                ):
                    return TaskClaim(
                        ClaimDisposition.BUSY,
                        task_id=task.id,
                        reason="publisher has not committed its acknowledgement",
                    )
            else:
                return TaskClaim(
                    ClaimDisposition.REJECTED,
                    task_id=task.id,
                    reason=f"unsupported task state: {task.status}",
                )

            task.status = "running"
            task.attempt_count += 1
            task.last_event_id = str(event.event_id)
            task.lease_owner = owner
            task.lease_expires_at = now + timedelta(seconds=lease_seconds)
            task.started_at = task.started_at or now
            return TaskClaim(
                ClaimDisposition.CLAIMED,
                task_id=task.id,
                lease_owner=owner,
            )

    async def complete_success(self, *, task_id: str, owner: str) -> None:
        now = utc_now()
        async with self._session_factory.begin() as session:
            task, asset, version = await _locked_task_graph(session, task_id)
            _require_processing_lease(task, owner)
            task.status = "succeeded"
            task.lease_owner = None
            task.lease_expires_at = None
            task.finished_at = now
            task.last_error_code = None
            task.last_error_message = None
            version.ingest_status = "ready"
            asset.status = "ready"

    async def renew_processing_lease(
        self,
        *,
        task_id: str,
        owner: str,
        lease_seconds: int,
    ) -> None:
        async with self._session_factory.begin() as session:
            task = await _locked_task(session, task_id)
            _require_processing_lease(task, owner)
            task.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)

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
    ) -> FailureTransition:
        now = utc_now()
        async with self._session_factory.begin() as session:
            task, asset, version = await _locked_task_graph(session, task_id)
            _require_processing_lease(task, owner)
            task.last_error_code = code[:64]
            task.last_error_message = message[:2000]
            task.lease_owner = None
            task.lease_expires_at = None
            task.published_at = None
            task.kafka_topic = None
            task.kafka_partition = None
            task.kafka_offset = None
            if retryable and task.attempt_count < task.max_attempts:
                delay = retry_delay_seconds(
                    attempt_count=task.attempt_count,
                    base_seconds=retry_base_seconds,
                    maximum_seconds=retry_max_seconds,
                )
                task.status = "retry"
                task.available_at = now + timedelta(seconds=delay)
            else:
                _mark_terminal_failure(
                    task,
                    asset,
                    version,
                    now=now,
                    code=code,
                    message=message,
                )
            return FailureTransition(task.status, task.available_at)


async def _locked_task(session: AsyncSession, task_id: str) -> IngestTask:
    task = await session.scalar(
        select(IngestTask).where(IngestTask.id == task_id).with_for_update()
    )
    if task is None:
        raise LeaseLostError(f"task {task_id} no longer exists")
    return task


async def _locked_task_graph(
    session: AsyncSession,
    task_id: str,
) -> tuple[IngestTask, Asset, AssetVersion]:
    row = (
        await session.execute(
            select(IngestTask, Asset, AssetVersion)
            .join(Asset, Asset.id == IngestTask.asset_id)
            .join(AssetVersion, AssetVersion.id == IngestTask.asset_version_id)
            .where(IngestTask.id == task_id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise LeaseLostError(f"task {task_id} no longer exists")
    return row


def _require_lease(task: IngestTask, owner: str) -> None:
    if task.lease_owner != owner:
        raise LeaseLostError(f"task {task.id} lease belongs to another worker")


def _require_processing_lease(task: IngestTask, owner: str) -> None:
    _require_lease(task, owner)
    if task.status != "running":
        raise LeaseLostError(f"task {task.id} is no longer running")


def _outbox_task(
    task: IngestTask,
    asset: Asset,
    version: AssetVersion,
) -> OutboxTask:
    return OutboxTask(
        task_id=task.id,
        status=task.status,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        dedupe_key=task.dedupe_key,
        tenant_id=asset.tenant_id,
        asset_id=asset.id,
        asset_version_id=version.id,
        version_number=version.version_number,
        object_key=version.object_key,
        content_type=version.media_type,
        size_bytes=version.size_bytes,
        content_sha256=version.content_sha256,
        error_code=task.last_error_code,
        error_message=task.last_error_message,
    )


def _event_mismatch(
    event: IngestTaskEvent,
    task: IngestTask,
    asset: Asset,
    version: AssetVersion,
) -> str | None:
    expected = {
        "task_id": task.id,
        "dedupe_key": task.dedupe_key,
        "tenant_id": asset.tenant_id,
        "asset_id": asset.id,
        "asset_version_id": version.id,
        "version_number": version.version_number,
        "object_key": version.object_key,
        "content_type": version.media_type,
        "size_bytes": version.size_bytes,
        "content_sha256": version.content_sha256,
        "max_attempts": task.max_attempts,
    }
    actual = {
        "task_id": str(event.task_id),
        "dedupe_key": event.dedupe_key,
        "tenant_id": event.tenant_id,
        "asset_id": str(event.asset_id),
        "asset_version_id": str(event.asset_version_id),
        "version_number": event.version_number,
        "object_key": event.object_key,
        "content_type": event.content_type,
        "size_bytes": event.size_bytes,
        "content_sha256": event.content_sha256,
        "max_attempts": event.max_attempts,
    }
    mismatches = [name for name, value in expected.items() if actual[name] != value]
    if mismatches:
        return "event identity mismatch: " + ", ".join(mismatches)
    return None


def _mark_terminal_failure(
    task: IngestTask,
    asset: Asset,
    version: AssetVersion,
    *,
    now: datetime,
    code: str,
    message: str,
) -> None:
    task.status = "failed"
    task.available_at = now
    task.finished_at = now
    task.published_at = None
    task.lease_owner = None
    task.lease_expires_at = None
    task.last_error_code = code[:64]
    task.last_error_message = message[:2000]
    asset.status = "failed"
    version.ingest_status = "failed"
