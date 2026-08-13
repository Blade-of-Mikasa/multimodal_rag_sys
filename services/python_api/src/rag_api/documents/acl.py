"""Resolve the authoritative asset ACL from MySQL at processing time."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_api.db.models import Asset
from rag_api.documents.domain import AssetIdentityError
from rag_api.kafka.contracts import IngestTaskEvent


class SqlAlchemyAssetAclResolver:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._session_factory = session_factory

    async def resolve_acl_id(self, event: IngestTaskEvent) -> str:
        async with self._session_factory() as session:
            acl_id = await session.scalar(
                select(Asset.acl_id).where(
                    Asset.id == str(event.asset_id),
                    Asset.tenant_id == event.tenant_id,
                    Asset.deleted_at.is_(None),
                )
            )
        if acl_id is None:
            raise AssetIdentityError(
                "asset does not exist in the event tenant or has been deleted"
            )
        return str(acl_id)
