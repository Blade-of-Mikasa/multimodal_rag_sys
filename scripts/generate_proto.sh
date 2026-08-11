#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${repository_root}/.venv"
python_output="${repository_root}/build/generated/python"
cpp_build_dir="${repository_root}/build/cpp"
conan_toolchain="${repository_root}/build/conan/conan_toolchain.cmake"
conan_build_environment="${repository_root}/build/conan/conanbuild.sh"
proto_file="${repository_root}/proto/rag_core.proto"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  echo "Missing .venv. Run ./scripts/bootstrap_dependencies.sh first." >&2
  exit 1
fi

if [[ ! -f "${conan_toolchain}" ]]; then
  echo "Missing Conan toolchain. Run ./scripts/bootstrap_dependencies.sh first." >&2
  exit 1
fi

if [[ ! -f "${conan_build_environment}" ]]; then
  echo "Missing Conan build environment. Run ./scripts/bootstrap_dependencies.sh first." >&2
  exit 1
fi

# Conan owns the CMake binary as a pinned build requirement.
source "${conan_build_environment}"

mkdir -p "${python_output}"
"${venv_dir}/bin/python" -m grpc_tools.protoc \
  "--proto_path=${repository_root}/proto" \
  "--python_out=${python_output}" \
  "--pyi_out=${python_output}" \
  "--grpc_python_out=${python_output}" \
  "${proto_file}"

cmake \
  -S "${repository_root}" \
  -B "${cpp_build_dir}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="${conan_toolchain}" \
  -DRAG_ENABLE_GRPC=ON

cmake \
  --build "${cpp_build_dir}" \
  --parallel

"${venv_dir}/bin/python" \
  "${repository_root}/scripts/verify_generated_contract.py" \
  "${python_output}"

echo "Generated Python contract: ${python_output}"
echo "Generated C++ contract: ${cpp_build_dir}/generated/cpp"
