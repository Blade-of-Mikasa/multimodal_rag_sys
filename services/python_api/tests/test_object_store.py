from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from botocore.exceptions import ClientError

from rag_api.config import Settings
from rag_api.storage import ObjectNotFoundError, ObjectTooLargeError, S3ObjectStore


class FakeBody:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass

    async def iter_chunks(self, *, chunk_size: int):
        self.chunk_size = chunk_size
        for chunk in self.chunks:
            yield chunk


class FakeClientContext:
    def __init__(self, client: object) -> None:
        self.client = client

    async def __aenter__(self) -> object:
        return self.client

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass


class FakeS3Client:
    def __init__(self) -> None:
        self.presign_call: dict[str, object] | None = None
        self.head_call: dict[str, object] | None = None
        self.delete_call: dict[str, object] | None = None
        self.get_call: dict[str, object] | None = None
        self.get_result: dict[str, object] | Exception = {
            "ContentLength": 6,
            "Body": FakeBody((b"abc", b"def")),
        }
        self.head_result: dict[str, object] | Exception = {
            "ContentLength": 123,
            "ContentType": "application/pdf",
            "ChecksumSHA256": "checksum",
            "ETag": '"etag-1"',
            "VersionId": "version-1",
            "Metadata": {"asset-id": "asset-1"},
        }

    async def generate_presigned_url(self, method: str, **arguments) -> str:
        self.presign_call = {"method": method, **arguments}
        return "https://storage.example/upload"

    async def head_object(self, **arguments) -> dict[str, object]:
        self.head_call = arguments
        if isinstance(self.head_result, Exception):
            raise self.head_result
        return self.head_result

    async def delete_object(self, **arguments) -> None:
        self.delete_call = arguments

    async def get_object(self, **arguments) -> dict[str, object]:
        self.get_call = arguments
        if isinstance(self.get_result, Exception):
            raise self.get_result
        return self.get_result


class FakeSession:
    def __init__(self, client: FakeS3Client) -> None:
        self.client = client
        self.create_arguments: dict[str, object] | None = None

    def create_client(self, service: str, **arguments) -> FakeClientContext:
        self.create_arguments = {"service": service, **arguments}
        return FakeClientContext(self.client)


class S3ObjectStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeS3Client()
        self.session = FakeSession(self.client)
        self.store = S3ObjectStore(
            Settings(
                environment="test",
                object_storage_endpoint_url="https://storage.example",
                object_storage_bucket="rag-assets",
                object_storage_access_key="access-key",
                object_storage_secret_key="secret-key",
                upload_url_expires_seconds=600,
                _env_file=None,
            ),
            self.session,
        )

    def test_presign_put_signs_size_type_checksum_and_identity(self) -> None:
        upload = asyncio.run(
            self.store.presign_put(
                object_key="tenants/t/assets/a/versions/1/source",
                content_type="application/pdf",
                size_bytes=123,
                checksum_sha256_base64="checksum",
                metadata={"asset-id": "asset-1"},
            )
        )

        params = self.client.presign_call["Params"]
        self.assertEqual(123, params["ContentLength"])
        self.assertEqual("checksum", params["ChecksumSHA256"])
        self.assertEqual("*", params["IfNoneMatch"])
        self.assertEqual("asset-1", params["Metadata"]["asset-id"])
        self.assertEqual(600, self.client.presign_call["ExpiresIn"])
        self.assertEqual(
            "checksum", upload.required_headers["x-amz-checksum-sha256"]
        )
        self.assertEqual("123", upload.required_headers["Content-Length"])
        self.assertEqual("*", upload.required_headers["If-None-Match"])
        self.assertEqual(
            "asset-1", upload.required_headers["x-amz-meta-asset-id"]
        )
        self.assertEqual("s3", self.session.create_arguments["service"])

    def test_head_requests_checksum_and_normalizes_etag(self) -> None:
        stored = asyncio.run(self.store.head("objects/1"))

        self.assertEqual(
            {
                "Bucket": "rag-assets",
                "Key": "objects/1",
                "ChecksumMode": "ENABLED",
            },
            self.client.head_call,
        )
        self.assertEqual("etag-1", stored.etag)
        self.assertEqual("checksum", stored.checksum_sha256_base64)

    def test_head_maps_provider_404_to_object_not_found(self) -> None:
        self.client.head_result = ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )

        with self.assertRaises(ObjectNotFoundError):
            asyncio.run(self.store.head("objects/missing"))

    def test_delete_targets_configured_bucket(self) -> None:
        asyncio.run(self.store.delete("objects/invalid"))

        self.assertEqual(
            {"Bucket": "rag-assets", "Key": "objects/invalid"},
            self.client.delete_call,
        )

    def test_download_streams_with_a_hard_limit(self) -> None:
        payload = asyncio.run(self.store.download("objects/1", max_bytes=6))

        self.assertEqual(b"abcdef", payload)
        self.assertEqual(
            {"Bucket": "rag-assets", "Key": "objects/1"},
            self.client.get_call,
        )

        with self.assertRaises(ObjectTooLargeError):
            asyncio.run(self.store.download("objects/1", max_bytes=5))

    def test_download_to_file_streams_and_hashes_without_buffering(self) -> None:
        with TemporaryDirectory() as temporary:
            destination = Path(temporary) / "source.mp4"
            result = asyncio.run(
                self.store.download_to_file(
                    "objects/1", destination=destination, max_bytes=6
                )
            )

            self.assertEqual(b"abcdef", destination.read_bytes())
            self.assertEqual(6, result.size_bytes)
            self.assertEqual(
                "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721",
                result.content_sha256,
            )

        with TemporaryDirectory() as temporary:
            destination = Path(temporary) / "partial.mp4"
            with self.assertRaises(ObjectTooLargeError):
                asyncio.run(
                    self.store.download_to_file(
                        "objects/1", destination=destination, max_bytes=5
                    )
                )
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
