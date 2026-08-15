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
    core_grpc_index_timeout_seconds: float = 60.0
    core_grpc_index_batch_max_bytes: int = 3_000_000
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
    kafka_bootstrap_servers: str = "127.0.0.1:9092"
    kafka_client_id: str = "multimodal-rag"
    kafka_ingest_topic: str = "rag.ingest.v1"
    kafka_retry_topic: str = "rag.ingest.retry.v1"
    kafka_dlq_topic: str = "rag.ingest.dlq.v1"
    kafka_consumer_group: str = "multimodal-rag-ingest-v1"
    kafka_security_protocol: Literal[
        "PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"
    ] = "PLAINTEXT"
    kafka_sasl_mechanism: Literal[
        "PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"
    ] = "PLAIN"
    kafka_sasl_username: SecretStr | None = None
    kafka_sasl_password: SecretStr | None = None
    kafka_outbox_batch_size: int = 100
    kafka_publish_lease_seconds: int = 30
    kafka_processing_lease_seconds: int = 300
    kafka_retry_base_seconds: int = 5
    kafka_retry_max_seconds: int = 900
    embedding_endpoint_url: str = "http://127.0.0.1:8080/v1/embeddings"
    embedding_api_key: SecretStr | None = None
    embedding_model_id: str = "embedding-general"
    embedding_model_version: str = "local"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 32
    embedding_timeout_seconds: float = 30.0
    document_download_max_bytes: int = 100_000_000
    document_chunk_max_chars: int = 1_600
    document_chunk_overlap_chars: int = 200
    vision_endpoint_url: str = "http://127.0.0.1:8080/v1/responses"
    vision_api_key: SecretStr | None = None
    vision_model_id: str = "vision-general"
    vision_model_version: str = "local"
    vision_timeout_seconds: float = 60.0
    vision_caption_max_bytes: int = 8_192
    vision_ocr_max_bytes: int = 49_152
    image_download_max_bytes: int = 20_000_000
    image_max_pixels: int = 25_000_000
    image_model_max_dimension: int = 4_096
    image_model_max_bytes: int = 10_000_000
    speech_endpoint_url: str = "http://127.0.0.1:8080/v1/audio/transcriptions"
    speech_api_key: SecretStr | None = None
    speech_model_id: str = "speech-to-text-general"
    speech_model_version: str = "local"
    speech_language: str | None = None
    speech_timeout_seconds: float = 600.0
    speech_max_segments_per_chunk: int = 10_000
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    video_command_timeout_seconds: float = 1_800.0
    video_download_max_bytes: int = 2_000_000_000
    video_max_duration_seconds: int = 14_400
    video_max_pixels: int = 50_000_000
    video_max_dimension: int = 16_384
    video_audio_chunk_seconds: int = 480
    video_scene_threshold: float = 0.35
    video_keyframe_max_gap_seconds: int = 60
    video_max_keyframes: int = 240

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

    @field_validator("core_grpc_index_timeout_seconds")
    @classmethod
    def validate_core_grpc_index_timeout(cls, value: float) -> float:
        if not 1 <= value <= 600:
            raise ValueError(
                "core_grpc_index_timeout_seconds must be between 1 and 600"
            )
        return value

    @field_validator("core_grpc_index_batch_max_bytes")
    @classmethod
    def validate_core_grpc_index_batch_size(cls, value: int) -> int:
        if not 65_536 <= value <= 3_500_000:
            raise ValueError(
                "core_grpc_index_batch_max_bytes must be between 65536 and 3500000"
            )
        return value

    @field_validator("embedding_endpoint_url")
    @classmethod
    def validate_embedding_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("embedding_endpoint_url must be an HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("embedding_model_id", "embedding_model_version")
    @classmethod
    def validate_embedding_identity(cls, value: str) -> str:
        if not value.strip() or len(value) > 256:
            raise ValueError(
                "embedding model identity must contain between 1 and 256 characters"
            )
        return value

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if not 1 <= value <= 65_536:
            raise ValueError("embedding_dimension must be between 1 and 65536")
        return value

    @field_validator("embedding_batch_size")
    @classmethod
    def validate_embedding_batch_size(cls, value: int) -> int:
        if not 1 <= value <= 512:
            raise ValueError("embedding_batch_size must be between 1 and 512")
        return value

    @field_validator("embedding_timeout_seconds")
    @classmethod
    def validate_embedding_timeout(cls, value: float) -> float:
        if not 0.1 <= value <= 600:
            raise ValueError(
                "embedding_timeout_seconds must be between 0.1 and 600"
            )
        return value

    @field_validator("document_download_max_bytes")
    @classmethod
    def validate_document_download_limit(cls, value: int) -> int:
        if not 1 <= value <= 5_000_000_000:
            raise ValueError(
                "document_download_max_bytes must be between 1 and 5000000000"
            )
        return value

    @field_validator("document_chunk_max_chars")
    @classmethod
    def validate_document_chunk_size(cls, value: int) -> int:
        if not 128 <= value <= 16_000:
            raise ValueError(
                "document_chunk_max_chars must be between 128 and 16000"
            )
        return value

    @model_validator(mode="after")
    def validate_document_chunk_overlap(self) -> "Settings":
        if not 0 <= self.document_chunk_overlap_chars < self.document_chunk_max_chars:
            raise ValueError(
                "document_chunk_overlap_chars must be non-negative and smaller "
                "than document_chunk_max_chars"
            )
        return self

    @field_validator("vision_endpoint_url")
    @classmethod
    def validate_vision_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("vision_endpoint_url must be an HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("vision_model_id", "vision_model_version")
    @classmethod
    def validate_vision_identity(cls, value: str) -> str:
        if not value.strip() or len(value) > 256:
            raise ValueError(
                "vision model identity must contain between 1 and 256 characters"
            )
        return value

    @field_validator("vision_timeout_seconds")
    @classmethod
    def validate_vision_timeout(cls, value: float) -> float:
        if not 0.1 <= value <= 600:
            raise ValueError("vision_timeout_seconds must be between 0.1 and 600")
        return value

    @field_validator("vision_caption_max_bytes", "vision_ocr_max_bytes")
    @classmethod
    def validate_vision_text_limit(cls, value: int) -> int:
        if not 256 <= value <= 60_000:
            raise ValueError("vision text byte limits must be between 256 and 60000")
        return value

    @model_validator(mode="after")
    def validate_vision_combined_text_limit(self) -> "Settings":
        if self.vision_caption_max_bytes + self.vision_ocr_max_bytes > 60_000:
            raise ValueError(
                "combined vision caption and OCR byte limits must not exceed 60000"
            )
        return self

    @field_validator("image_download_max_bytes", "image_model_max_bytes")
    @classmethod
    def validate_image_byte_limit(cls, value: int) -> int:
        if not 1 <= value <= 100_000_000:
            raise ValueError("image byte limits must be between 1 and 100000000")
        return value

    @model_validator(mode="after")
    def validate_image_model_byte_limit(self) -> "Settings":
        if self.image_model_max_bytes > self.image_download_max_bytes:
            raise ValueError(
                "image_model_max_bytes must not exceed image_download_max_bytes"
            )
        return self

    @field_validator("image_max_pixels")
    @classmethod
    def validate_image_pixel_limit(cls, value: int) -> int:
        if not 1_000_000 <= value <= 100_000_000:
            raise ValueError("image_max_pixels must be between 1000000 and 100000000")
        return value

    @field_validator("image_model_max_dimension")
    @classmethod
    def validate_image_dimension_limit(cls, value: int) -> int:
        if not 512 <= value <= 16_384:
            raise ValueError(
                "image_model_max_dimension must be between 512 and 16384"
            )
        return value

    @field_validator("speech_endpoint_url")
    @classmethod
    def validate_speech_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("speech_endpoint_url must be an HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("speech_model_id", "speech_model_version")
    @classmethod
    def validate_speech_identity(cls, value: str) -> str:
        if not value.strip() or len(value) > 256:
            raise ValueError(
                "speech model identity must contain between 1 and 256 characters"
            )
        return value

    @field_validator("speech_language")
    @classmethod
    def validate_speech_language(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value or len(value) > 32 or not value.isascii():
            raise ValueError("speech_language must be a short ASCII language code")
        return value

    @field_validator("speech_timeout_seconds", "video_command_timeout_seconds")
    @classmethod
    def validate_video_timeout(cls, value: float) -> float:
        if not 1 <= value <= 3_600:
            raise ValueError("video and speech timeouts must be between 1 and 3600")
        return value

    @field_validator("speech_max_segments_per_chunk")
    @classmethod
    def validate_speech_segment_limit(cls, value: int) -> int:
        if not 1 <= value <= 100_000:
            raise ValueError("speech segment limit must be between 1 and 100000")
        return value

    @field_validator("ffmpeg_binary", "ffprobe_binary")
    @classmethod
    def validate_video_binary(cls, value: str) -> str:
        if not value.strip() or len(value) > 4_096 or "\x00" in value:
            raise ValueError("video binary path must not be blank")
        return value

    @field_validator("video_download_max_bytes")
    @classmethod
    def validate_video_byte_limit(cls, value: int) -> int:
        if not 1 <= value <= 5_000_000_000:
            raise ValueError("video byte limit must be between 1 and 5000000000")
        return value

    @field_validator("video_max_duration_seconds")
    @classmethod
    def validate_video_duration(cls, value: int) -> int:
        if not 1 <= value <= 86_400:
            raise ValueError("video duration must be between 1 and 86400 seconds")
        return value

    @field_validator("video_max_pixels")
    @classmethod
    def validate_video_pixels(cls, value: int) -> int:
        if not 1_000_000 <= value <= 268_435_456:
            raise ValueError("video pixel limit must be between 1000000 and 268435456")
        return value

    @field_validator("video_max_dimension")
    @classmethod
    def validate_video_dimension(cls, value: int) -> int:
        if not 512 <= value <= 32_768:
            raise ValueError("video dimension limit must be between 512 and 32768")
        return value

    @field_validator("video_audio_chunk_seconds")
    @classmethod
    def validate_audio_chunk_duration(cls, value: int) -> int:
        if not 30 <= value <= 750:
            raise ValueError("audio chunks must be between 30 and 750 seconds")
        return value

    @field_validator("video_scene_threshold")
    @classmethod
    def validate_scene_threshold(cls, value: float) -> float:
        if not 0.01 <= value <= 0.99:
            raise ValueError("video scene threshold must be between 0.01 and 0.99")
        return value

    @field_validator("video_keyframe_max_gap_seconds")
    @classmethod
    def validate_keyframe_gap(cls, value: int) -> int:
        if not 5 <= value <= 600:
            raise ValueError("video keyframe gap must be between 5 and 600 seconds")
        return value

    @field_validator("video_max_keyframes")
    @classmethod
    def validate_keyframe_limit(cls, value: int) -> int:
        if not 1 <= value <= 2_000:
            raise ValueError("video keyframe limit must be between 1 and 2000")
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

    @field_validator("kafka_bootstrap_servers")
    @classmethod
    def validate_kafka_bootstrap_servers(cls, value: str) -> str:
        servers = [server.strip() for server in value.split(",")]
        if not servers or any(not _is_valid_kafka_server(server) for server in servers):
            raise ValueError(
                "kafka_bootstrap_servers must be a comma-separated host:port list"
            )
        return ",".join(servers)

    @field_validator(
        "kafka_client_id",
        "kafka_consumer_group",
        "kafka_ingest_topic",
        "kafka_retry_topic",
        "kafka_dlq_topic",
    )
    @classmethod
    def validate_kafka_names(cls, value: str) -> str:
        if not value or len(value) > 249:
            raise ValueError("Kafka names must contain between 1 and 249 characters")
        if not value.isascii() or any(
            not (character.isalnum() or character in "._-") for character in value
        ):
            raise ValueError("Kafka names contain unsupported characters")
        return value

    @field_validator("kafka_outbox_batch_size")
    @classmethod
    def validate_kafka_batch_size(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("kafka_outbox_batch_size must be between 1 and 1000")
        return value

    @field_validator(
        "kafka_publish_lease_seconds",
        "kafka_processing_lease_seconds",
        "kafka_retry_base_seconds",
        "kafka_retry_max_seconds",
    )
    @classmethod
    def validate_kafka_durations(cls, value: int) -> int:
        if not 1 <= value <= 86_400:
            raise ValueError("Kafka durations must be between 1 and 86400 seconds")
        return value

    @model_validator(mode="after")
    def validate_kafka_settings(self) -> "Settings":
        if len(
            {
                self.kafka_ingest_topic,
                self.kafka_retry_topic,
                self.kafka_dlq_topic,
            }
        ) != 3:
            raise ValueError("Kafka ingest, retry, and DLQ topics must be distinct")
        if (self.kafka_sasl_username is None) != (
            self.kafka_sasl_password is None
        ):
            raise ValueError("Kafka SASL username and password must be set together")
        if self.kafka_security_protocol.startswith("SASL_") and (
            self.kafka_sasl_username is None
        ):
            raise ValueError("Kafka SASL credentials are required by security protocol")
        if not self.kafka_security_protocol.startswith("SASL_") and (
            self.kafka_sasl_username is not None
        ):
            raise ValueError("Kafka SASL credentials require a SASL security protocol")
        if self.kafka_retry_base_seconds > self.kafka_retry_max_seconds:
            raise ValueError("Kafka retry base must not exceed retry maximum")
        return self

    @property
    def kafka_bootstrap_server_list(self) -> list[str]:
        return self.kafka_bootstrap_servers.split(",")


def _is_valid_kafka_server(server: str) -> bool:
    host, separator, port_text = server.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        return False
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return False
    return 1 <= int(port_text) <= 65_535
