"""ORM models for assets, ingestion, conversations, and reusable ACLs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from rag_api.db.base import (
    MYSQL_TABLE_OPTIONS,
    Base,
    TimestampMixin,
    UuidPrimaryKeyMixin,
)


class AccessControlList(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-scoped ACL that can be reused by assets and conversations."""

    __tablename__ = "access_control_lists"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class AccessControlEntry(UuidPrimaryKeyMixin, Base):
    """One subject-to-permission binding in an ACL."""

    __tablename__ = "access_control_entries"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('user', 'group', 'service')",
            name="ck_access_control_entries_subject_type",
        ),
        CheckConstraint(
            "permission IN ('read', 'write', 'admin')",
            name="ck_access_control_entries_permission",
        ),
        UniqueConstraint("acl_id", "subject_type", "subject_id", "permission"),
        Index(
            "ix_access_control_entries_subject",
            "subject_type",
            "subject_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    acl_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("access_control_lists.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class Asset(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Logical user-owned asset; bytes live in object storage."""

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'deleted')",
            name="ck_assets_status",
        ),
        CheckConstraint(
            "latest_version_number >= 0",
            name="ck_assets_latest_version_number",
        ),
        Index("ix_assets_tenant_status", "tenant_id", "status", "created_at"),
        Index("ix_assets_tenant_owner", "tenant_id", "owner_user_id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    acl_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("access_control_lists.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    latest_version_number: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )


class AssetVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable object-storage version plus its ingestion state."""

    __tablename__ = "asset_versions"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="ck_asset_versions_number"),
        CheckConstraint("size_bytes >= 0", name="ck_asset_versions_size_bytes"),
        CheckConstraint(
            "ingest_status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_asset_versions_ingest_status",
        ),
        UniqueConstraint("asset_id", "version_number"),
        Index("ix_asset_versions_ingest_status", "ingest_status", "created_at"),
        Index("ix_asset_versions_content_sha256", "content_sha256"),
        MYSQL_TABLE_OPTIONS,
    )

    asset_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_sha256: Mapped[str] = mapped_column(mysql.CHAR(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
    )
    media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    ingest_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    attributes: Mapped[dict[str, Any] | None] = mapped_column(
        "attributes_json",
        mysql.JSON,
        nullable=True,
    )


class IngestTask(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotent Kafka-backed ingestion task state."""

    __tablename__ = "ingest_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'retry', 'succeeded', 'failed', "
            "'dead_letter')",
            name="ck_ingest_tasks_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 "
            "AND attempt_count <= max_attempts",
            name="ck_ingest_tasks_attempts",
        ),
        UniqueConstraint("dedupe_key"),
        Index("ix_ingest_tasks_dispatch", "status", "available_at"),
        Index(
            "ix_ingest_tasks_outbox",
            "published_at",
            "status",
            "available_at",
            "lease_expires_at",
        ),
        Index("ix_ingest_tasks_version_type", "asset_version_id", "task_type"),
        MYSQL_TABLE_OPTIONS,
    )

    asset_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_version_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("asset_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'queued'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=False,
        server_default=text("5"),
    )
    dedupe_key: Mapped[str] = mapped_column(String(191), nullable=False)
    kafka_topic: Mapped[str | None] = mapped_column(String(249), nullable=True)
    kafka_partition: Mapped[int | None] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=True,
    )
    kafka_offset: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
    last_event_id: Mapped[str | None] = mapped_column(
        mysql.CHAR(36),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_publish_error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    available_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )


class Conversation(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped RAG conversation protected by an ACL."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_conversations_status",
        ),
        Index(
            "ix_conversations_tenant_owner",
            "tenant_id",
            "owner_user_id",
            "updated_at",
        ),
        Index(
            "ix_conversations_tenant_status",
            "tenant_id",
            "status",
            "updated_at",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    acl_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("access_control_lists.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )


class ConversationMessage(UuidPrimaryKeyMixin, Base):
    """Immutable message and its evidence citation snapshot."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint(
            "`role` IN ('system', 'user', 'assistant', 'tool')",
            name="ck_conversation_messages_role",
        ),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="ck_conversation_messages_token_count",
        ),
        Index(
            "ix_conversation_messages_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index("ix_conversation_messages_request_id", "request_id"),
        MYSQL_TABLE_OPTIONS,
    )

    conversation_id: Mapped[str] = mapped_column(
        mysql.CHAR(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(mysql.LONGTEXT, nullable=False)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(
        "citations_json",
        mysql.JSON,
        nullable=True,
    )
    token_count: Mapped[int | None] = mapped_column(
        mysql.INTEGER(unsigned=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
