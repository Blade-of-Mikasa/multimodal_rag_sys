"""Typed runtime configuration for the Python API process."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
