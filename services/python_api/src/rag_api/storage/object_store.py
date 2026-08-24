"""S3-compatible object storage behind a small application-owned port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from aiobotocore.config import AioConfig
from aiobotocore.session import AioSession, get_session
from botocore.exceptions import BotoCoreError, ClientError

from rag_api.config import Settings


class ObjectStoreError(RuntimeError):
    """The object provider could not complete an operation."""


class ObjectNotFoundError(ObjectStoreError):
    """The requested object key does not exist."""


class ObjectTooLargeError(ObjectStoreError):
    """The object exceeded the caller's bounded download budget."""


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    required_headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredObject:
    size_bytes: int
    content_type: str
    checksum_sha256_base64: str | None
    etag: str | None
    version_id: str | None
    metadata: dict[str, str]


class ObjectStore(Protocol):
    async def presign_put(
        self,
        *,
        object_key: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256_base64: str,
        metadata: dict[str, str],
    ) -> PresignedUpload: ...

    async def head(self, object_key: str) -> StoredObject: ...

    async def download(self, object_key: str, *, max_bytes: int) -> bytes: ...

    async def delete(self, object_key: str) -> None: ...


class S3ObjectStore:
    """Async S3 adapter supporting AWS S3 and compatible endpoints."""

    def __init__(
        self,
        settings: Settings,
        session: AioSession | None = None,
    ) -> None:
        self._settings = settings
        self._session = session or get_session()

    def _client_arguments(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "region_name": self._settings.object_storage_region,
            "endpoint_url": self._settings.object_storage_endpoint_url,
            "config": AioConfig(
                signature_version="s3v4",
                s3={
                    "addressing_style": (
                        self._settings.object_storage_addressing_style
                    )
                },
                retries={"mode": "standard", "max_attempts": 3},
            ),
        }
        access_key = self._settings.object_storage_access_key
        secret_key = self._settings.object_storage_secret_key
        if access_key is not None and secret_key is not None:
            arguments["aws_access_key_id"] = access_key.get_secret_value()
            arguments["aws_secret_access_key"] = secret_key.get_secret_value()
        session_token = self._settings.object_storage_session_token
        if session_token is not None:
            arguments["aws_session_token"] = session_token.get_secret_value()
        return arguments

    def _client(self) -> Any:
        return self._session.create_client("s3", **self._client_arguments())

    async def presign_put(
        self,
        *,
        object_key: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256_base64: str,
        metadata: dict[str, str],
    ) -> PresignedUpload:
        params = {
            "Bucket": self._settings.object_storage_bucket,
            "Key": object_key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
            "ChecksumSHA256": checksum_sha256_base64,
            "IfNoneMatch": "*",
            "Metadata": metadata,
        }
        try:
            async with self._client() as client:
                url = await client.generate_presigned_url(
                    "put_object",
                    Params=params,
                    ExpiresIn=self._settings.upload_url_expires_seconds,
                    HttpMethod="PUT",
                )
        except (BotoCoreError, ClientError, ValueError) as error:
            raise ObjectStoreError("failed to create upload URL") from error

        headers = {
            "Content-Length": str(size_bytes),
            "Content-Type": content_type,
            "If-None-Match": "*",
            "x-amz-checksum-sha256": checksum_sha256_base64,
        }
        headers.update(
            {f"x-amz-meta-{key}": value for key, value in metadata.items()}
        )
        return PresignedUpload(
            url=url,
            required_headers=headers,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self._settings.upload_url_expires_seconds),
        )

    async def head(self, object_key: str) -> StoredObject:
        try:
            async with self._client() as client:
                response = await client.head_object(
                    Bucket=self._settings.object_storage_bucket,
                    Key=object_key,
                    ChecksumMode="ENABLED",
                )
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            )
            code = error.response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(object_key) from error
            raise ObjectStoreError("failed to inspect uploaded object") from error
        except BotoCoreError as error:
            raise ObjectStoreError("failed to inspect uploaded object") from error

        return StoredObject(
            size_bytes=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "")),
            checksum_sha256_base64=response.get("ChecksumSHA256"),
            etag=_strip_etag(response.get("ETag")),
            version_id=response.get("VersionId"),
            metadata=dict(response.get("Metadata", {})),
        )

    async def download(self, object_key: str, *, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        try:
            async with self._client() as client:
                response = await client.get_object(
                    Bucket=self._settings.object_storage_bucket,
                    Key=object_key,
                )
                content_length = int(response.get("ContentLength", 0))
                if content_length > max_bytes:
                    raise ObjectTooLargeError(object_key)
                body = response["Body"]
                chunks: list[bytes] = []
                total = 0
                async with body:
                    async for chunk in body.iter_chunks(chunk_size=64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ObjectTooLargeError(object_key)
                        chunks.append(chunk)
        except ObjectTooLargeError:
            raise
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            )
            code = error.response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(object_key) from error
            raise ObjectStoreError("failed to download object") from error
        except (BotoCoreError, KeyError, TypeError, ValueError) as error:
            raise ObjectStoreError("failed to download object") from error
        return b"".join(chunks)

    async def delete(self, object_key: str) -> None:
        try:
            async with self._client() as client:
                await client.delete_object(
                    Bucket=self._settings.object_storage_bucket,
                    Key=object_key,
                )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStoreError("failed to delete uploaded object") from error


def _strip_etag(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip('"')
