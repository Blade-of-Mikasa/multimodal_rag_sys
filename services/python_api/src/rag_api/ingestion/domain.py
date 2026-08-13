"""Shared ingestion ports and bounded UTF-8 helpers."""

from __future__ import annotations

from typing import Protocol

from rag_api.kafka.contracts import IngestTaskEvent


class AssetIdentityError(RuntimeError):
    """The durable MySQL asset identity no longer matches the event."""


class AssetAclResolver(Protocol):
    async def resolve_acl_id(self, event: IngestTaskEvent) -> str: ...


def truncate_utf8(value: str, max_bytes: int) -> str:
    """Return a valid UTF-8 prefix no larger than ``max_bytes``."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
