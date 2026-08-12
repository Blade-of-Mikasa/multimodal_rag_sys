"""Transactional SQLAlchemy repository for the upload workflow."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_api.db.base import new_uuid
from rag_api.db.models import (
    AccessControlEntry,
    AccessControlList,
    Asset,
    AssetVersion,
    IngestTask,
)
from rag_api.uploads.domain import (
    CompletedUpload,
    PendingUpload,
    UploadNotFoundError,
    UploadNotReadyError,
)


class SqlAlchemyUploadRepository:
    """Persist upload state while keeping service logic storage-agnostic."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

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
    ) -> None:
        acl_id = new_uuid()
        async with self._session_factory.begin() as session:
            session.add_all(
                [
                    AccessControlList(
                        id=acl_id,
                        tenant_id=tenant_id,
                        name=f"asset:{asset_id}",
                    ),
                    AccessControlEntry(
                        id=new_uuid(),
                        acl_id=acl_id,
                        subject_type="user",
                        subject_id=owner_user_id,
                        permission="admin",
                    ),
                    Asset(
                        id=asset_id,
                        tenant_id=tenant_id,
                        owner_user_id=owner_user_id,
                        acl_id=acl_id,
                        name=file_name,
                        media_type=content_type,
                        status="pending",
                        latest_version_number=1,
                    ),
                    AssetVersion(
                        id=asset_version_id,
                        asset_id=asset_id,
                        version_number=1,
                        object_key=object_key,
                        content_sha256=content_sha256,
                        size_bytes=size_bytes,
                        media_type=content_type,
                        ingest_status="pending",
                    ),
                ]
            )

    async def find_upload(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
    ) -> PendingUpload | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Asset, AssetVersion)
                    .join(AssetVersion, AssetVersion.asset_id == Asset.id)
                    .where(
                        Asset.id == asset_id,
                        Asset.tenant_id == tenant_id,
                        Asset.owner_user_id == owner_user_id,
                        AssetVersion.version_number == version_number,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            asset, version = row
            task_id = await session.scalar(
                select(IngestTask.id).where(
                    IngestTask.asset_version_id == version.id,
                    IngestTask.task_type == "index_asset",
                )
            )
            return _pending_upload(asset, version, task_id)

    async def complete_upload(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
        storage_attributes: dict[str, Any],
    ) -> CompletedUpload:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(Asset, AssetVersion)
                    .join(AssetVersion, AssetVersion.asset_id == Asset.id)
                    .where(
                        Asset.id == asset_id,
                        Asset.tenant_id == tenant_id,
                        Asset.owner_user_id == owner_user_id,
                        AssetVersion.version_number == version_number,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise UploadNotFoundError(asset_id)
            asset, version = row

            task_id = await session.scalar(
                select(IngestTask.id).where(
                    IngestTask.asset_version_id == version.id,
                    IngestTask.task_type == "index_asset",
                )
            )
            if version.ingest_status in {"processing", "ready"} and task_id:
                return CompletedUpload(
                    asset_id=asset.id,
                    asset_version_id=version.id,
                    version_number=version.version_number,
                    asset_status=asset.status,
                    ingest_status=version.ingest_status,
                    ingest_task_id=task_id,
                )
            if version.ingest_status != "pending":
                raise UploadNotReadyError(version.ingest_status)

            task_id = new_uuid()
            asset.status = "processing"
            version.ingest_status = "processing"
            version.attributes = {
                **(version.attributes or {}),
                "storage": storage_attributes,
            }
            session.add(
                IngestTask(
                    id=task_id,
                    asset_id=asset.id,
                    asset_version_id=version.id,
                    task_type="index_asset",
                    status="queued",
                    attempt_count=0,
                    max_attempts=5,
                    dedupe_key=f"index_asset:{version.id}",
                )
            )
            return CompletedUpload(
                asset_id=asset.id,
                asset_version_id=version.id,
                version_number=version.version_number,
                asset_status=asset.status,
                ingest_status=version.ingest_status,
                ingest_task_id=task_id,
            )

    async def mark_upload_failed(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        asset_id: str,
        version_number: int,
        issues: dict[str, dict[str, object]],
    ) -> None:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(Asset, AssetVersion)
                    .join(AssetVersion, AssetVersion.asset_id == Asset.id)
                    .where(
                        Asset.id == asset_id,
                        Asset.tenant_id == tenant_id,
                        Asset.owner_user_id == owner_user_id,
                        AssetVersion.version_number == version_number,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise UploadNotFoundError(asset_id)
            asset, version = row
            if version.ingest_status != "pending":
                return
            asset.status = "failed"
            version.ingest_status = "failed"
            version.attributes = {
                **(version.attributes or {}),
                "upload_validation_issues": issues,
            }


def _pending_upload(
    asset: Asset,
    version: AssetVersion,
    task_id: str | None,
) -> PendingUpload:
    return PendingUpload(
        asset_id=asset.id,
        asset_version_id=version.id,
        version_number=version.version_number,
        object_key=version.object_key,
        file_name=asset.name,
        content_type=version.media_type,
        size_bytes=version.size_bytes,
        content_sha256=version.content_sha256,
        status=version.ingest_status,
        ingest_task_id=task_id,
    )
