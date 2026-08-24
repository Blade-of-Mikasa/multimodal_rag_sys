#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
milvus_build_dir="${repository_root}/build/cpp-milvus"

"${repository_root}/scripts/verify_core_service.sh"

PYTHONPATH="${repository_root}/services/python_api/src:${repository_root}/build/generated/python" \
  "${repository_root}/.venv/bin/python" -m unittest discover \
  -s "${repository_root}/services/python_api/tests" -v

source "${repository_root}/build/conan/conanbuild.sh"

cmake_arguments=(
  -S "${repository_root}"
  -B "${milvus_build_dir}"
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_TOOLCHAIN_FILE="${repository_root}/build/conan/conan_toolchain.cmake"
  -DRAG_ENABLE_GRPC=ON
  -DRAG_ENABLE_MILVUS=ON
)
if [[ -n "${RAG_MILVUS_SDK_SOURCE:-}" ]]; then
  cmake_arguments+=(
    -DFETCHCONTENT_SOURCE_DIR_MILVUS_SDK_CPP="${RAG_MILVUS_SDK_SOURCE}"
  )
fi
if [[ -n "${RAG_MILVUS_PROTO_SOURCE:-}" ]]; then
  cmake_arguments+=(
    -DFETCHCONTENT_SOURCE_DIR_MILVUS_PROTO="${RAG_MILVUS_PROTO_SOURCE}"
  )
fi

cmake "${cmake_arguments[@]}"
# CTest only registers tests during configure; their executables still need to be
# part of the build. Build the default target so the Milvus-enabled server and
# every registered contract/unit test are available below.
cmake --build "${milvus_build_dir}" --parallel
ctest --test-dir "${milvus_build_dir}" --output-on-failure

echo "M07 document ingestion and Milvus retrieval verification: PASS"
