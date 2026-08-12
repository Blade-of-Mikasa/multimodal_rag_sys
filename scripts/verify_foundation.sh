#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/multimodal-rag-foundation.XXXXXX")"
test_binary="${build_dir}/rag_core_domain_test"

cleanup() {
  rm -f "${test_binary}"
  rmdir "${build_dir}"
}
trap cleanup EXIT

for test_pattern in test_domain.py test_proto_contract.py; do
  PYTHONPATH="${repository_root}/services/python_api/src" \
    python3 -m unittest discover \
    -s "${repository_root}/services/python_api/tests" \
    -p "${test_pattern}" \
    -v
done

c++ \
  -std=c++20 \
  -Wall \
  -Wextra \
  -Werror \
  -pedantic \
  -I"${repository_root}/core/include" \
  "${repository_root}/core/src/domain.cpp" \
  "${repository_root}/core/tests/domain_test.cpp" \
  -o "${test_binary}"

"${test_binary}"
