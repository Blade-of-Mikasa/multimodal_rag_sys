#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${repository_root}/.venv/bin/python"

if [[ ! -x "${python_bin}" ]]; then
  echo "Missing .venv. Run ./scripts/bootstrap_dependencies.sh first." >&2
  exit 1
fi

export PYTHONPATH="${repository_root}/services/python_api/src"

"${python_bin}" -m pip check
"${python_bin}" -m unittest discover \
  -s "${repository_root}/services/python_api/tests" \
  -p "test_*.py" \
  -v
"${python_bin}" - <<'PY'
import asyncio
from urllib.parse import parse_qs, urlparse

from rag_api.config import Settings
from rag_api.storage import S3ObjectStore


async def verify_real_signer() -> None:
    store = S3ObjectStore(
        Settings(
            environment="test",
            object_storage_access_key="test-access",
            object_storage_secret_key="test-secret",
            _env_file=None,
        )
    )
    upload = await store.presign_put(
        object_key="tenants/test/assets/test/versions/1/source",
        content_type="application/pdf",
        size_bytes=3,
        checksum_sha256_base64=(
            "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="
        ),
        metadata={"asset-id": "test", "asset-version-id": "version"},
    )
    signed = parse_qs(urlparse(upload.url).query)["X-Amz-SignedHeaders"][0]
    expected = {
        "content-length",
        "content-type",
        "host",
        "if-none-match",
        "x-amz-checksum-sha256",
        "x-amz-meta-asset-id",
        "x-amz-meta-asset-version-id",
    }
    assert set(signed.split(";")) == expected, signed


asyncio.run(verify_real_signer())
PY

echo "M05 object storage and upload verification: PASS"
