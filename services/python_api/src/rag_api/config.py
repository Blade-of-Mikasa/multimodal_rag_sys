"""Typed runtime configuration for the Python API process."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    """Configuration loaded from ``RAG_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    service_name: str = "multimodal-rag-api"
    service_version: str = "0.1.0"
    environment: Environment = "local"
    api_prefix: str = "/api/v1"
    debug: bool = False
    core_grpc_target: str = "127.0.0.1:50051"
    core_grpc_timeout_seconds: float = 1.0
    mysql_dsn: SecretStr = SecretStr(
        "mysql+asyncmy://rag:rag@127.0.0.1:3306/"
        "multimodal_rag?charset=utf8mb4"
    )
    object_storage_endpoint_url: str | None = "http://127.0.0.1:9000"
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "multimodal-rag"
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    object_storage_session_token: SecretStr | None = None
    object_storage_addressing_style: Literal["path", "virtual"] = "path"
    upload_url_expires_seconds: int = 900
    upload_max_bytes: int = 5_000_000_000

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        if value == "/" or value.endswith("/"):
            raise ValueError("api_prefix must not end with '/'")
        return value

    @field_validator("core_grpc_target")
    @classmethod
    def validate_core_grpc_target(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("core_grpc_target must not be empty")
        return value

    @field_validator("core_grpc_timeout_seconds")
    @classmethod
    def validate_core_grpc_timeout(cls, value: float) -> float:
        if not 0.05 <= value <= 30:
            raise ValueError(
                "core_grpc_timeout_seconds must be between 0.05 and 30"
            )
        return value

    @field_validator("mysql_dsn")
    @classmethod
    def validate_mysql_dsn(cls, value: SecretStr) -> SecretStr:
        dsn = value.get_secret_value()
        try:
            url = make_url(dsn)
        except ArgumentError as error:
            raise ValueError("mysql_dsn must be a valid SQLAlchemy URL") from error
        if url.get_backend_name() != "mysql" or url.get_driver_name() != "asyncmy":
            raise ValueError("mysql_dsn must use the mysql+asyncmy driver")
        if not url.database:
            raise ValueError("mysql_dsn must include a database name")
        return value

    @field_validator("object_storage_endpoint_url")
    @classmethod
    def validate_storage_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "object_storage_endpoint_url must be an HTTP(S) URL or null"
            )
        return value.rstrip("/")

    @field_validator("object_storage_region", "object_storage_bucket")
    @classmethod
    def reject_blank_storage_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("object storage region and bucket must not be blank")
        return value

    @field_validator("upload_url_expires_seconds")
    @classmethod
    def validate_upload_expiry(cls, value: int) -> int:
        if not 60 <= value <= 3600:
            raise ValueError(
                "upload_url_expires_seconds must be between 60 and 3600"
            )
        return value

    @field_validator("upload_max_bytes")
    @classmethod
    def validate_upload_limit(cls, value: int) -> int:
        if not 1 <= value <= 5_000_000_000:
            raise ValueError("upload_max_bytes must be between 1 and 5000000000")
        return value

    @model_validator(mode="after")
    def validate_storage_credentials(self) -> "Settings":
        if (self.object_storage_access_key is None) != (
            self.object_storage_secret_key is None
        ):
            raise ValueError(
                "object storage access and secret keys must be set together"
            )
        if (
            self.object_storage_session_token is not None
            and self.object_storage_access_key is None
        ):
            raise ValueError(
                "object storage session token requires explicit access keys"
            )
        return self
