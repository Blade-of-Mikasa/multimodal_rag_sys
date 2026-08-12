#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${repository_root}/.venv"
conan_home="${RAG_CONAN_HOME:-${repository_root}/build/conan-home}"
conan_output="${repository_root}/build/conan"
cmake_bootstrap="${repository_root}/build/cmake-bootstrap"

select_python() {
  if [[ -n "${RAG_PYTHON:-}" ]]; then
    printf '%s\n' "${RAG_PYTHON}"
    return
  fi

  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return
    fi
  done

  echo "Python 3.11+ is required. Set RAG_PYTHON to a compatible interpreter." >&2
  return 1
}

python_bin="$(select_python)"
"${python_bin}" -c 'import sys; assert sys.version_info >= (3, 11), sys.version'

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  "${python_bin}" -m venv "${venv_dir}"
fi

"${venv_dir}/bin/python" -m pip install \
  --requirement "${repository_root}/requirements/tooling.lock"
"${venv_dir}/bin/python" -m pip install \
  --requirement "${repository_root}/requirements/api.lock"
"${venv_dir}/bin/python" -m pip install \
  --no-build-isolation \
  --no-deps \
  --editable "${repository_root}"

mkdir -p "${conan_home}" "${conan_output}" "${cmake_bootstrap}"
export CONAN_HOME="${conan_home}"

if ! "${venv_dir}/bin/conan" profile path default >/dev/null 2>&1; then
  "${venv_dir}/bin/conan" profile detect --name default
fi

# Install and activate CMake first. This explicit bootstrap is necessary when a
# dependency has no compatible binary and Conan must execute that recipe's
# CMake build before the root VirtualBuildEnv has been generated.
"${venv_dir}/bin/conan" install \
  --tool-requires cmake/4.4.0 \
  --output-folder "${cmake_bootstrap}" \
  --generator VirtualBuildEnv \
  --build=missing \
  --lockfile "${repository_root}/conan.lock"
source "${cmake_bootstrap}/conanbuild.sh"

"${venv_dir}/bin/conan" install "${repository_root}" \
  --output-folder "${conan_output}" \
  --settings build_type=Release \
  --build=missing \
  --lockfile "${repository_root}/conan.lock"

echo "Dependency bootstrap complete."
echo "Python: ${venv_dir}/bin/python"
echo "Conan toolchain: ${conan_output}/conan_toolchain.cmake"
echo "Build environment: ${conan_output}/conanbuild.sh"
