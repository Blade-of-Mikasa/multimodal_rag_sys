#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/multimodal-rag-m03.XXXXXX")"
server_log="${test_dir}/rag-core.log"
server_pid=""

cleanup() {
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill -TERM "${server_pid}"
    wait "${server_pid}" || true
  fi
  rm -f "${server_log}"
  rmdir "${test_dir}"
}
trap cleanup EXIT

"${repository_root}/scripts/verify_python_api.sh"
"${repository_root}/scripts/generate_proto.sh"

source "${repository_root}/build/conan/conanbuild.sh"
ctest \
  --test-dir "${repository_root}/build/cpp" \
  --output-on-failure

server_binary="${repository_root}/build/cpp/core/grpc/rag_core_server"
"${server_binary}" --listen 127.0.0.1:0 >"${server_log}" 2>&1 &
server_pid=$!

core_address=""
for _ in {1..100}; do
  core_address="$(sed -n 's/^RAG_CORE_READY address=//p' "${server_log}")"
  if [[ -n "${core_address}" ]]; then
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    cat "${server_log}" >&2
    echo "C++ Core exited before becoming ready." >&2
    exit 1
  fi
  sleep 0.05
done

if [[ -z "${core_address}" ]]; then
  cat "${server_log}" >&2
  echo "Timed out waiting for the C++ Core readiness marker." >&2
  exit 1
fi

PYTHONPATH="${repository_root}/services/python_api/src:${repository_root}/build/generated/python" \
RAG_CORE_TEST_TARGET="${core_address}" \
  "${repository_root}/.venv/bin/python" -m unittest \
  "${repository_root}/services/python_api/tests/test_core_integration.py" \
  -v

kill -TERM "${server_pid}"
wait "${server_pid}"
server_pid=""

echo "M03 C++ Core gRPC service verification: PASS"
