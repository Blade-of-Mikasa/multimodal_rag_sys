#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conan_build_environment="${repository_root}/build/conan/conanbuild.sh"

"${repository_root}/scripts/verify_foundation.sh"
"${repository_root}/scripts/generate_proto.sh"
source "${conan_build_environment}"
ctest \
  --test-dir "${repository_root}/build/cpp" \
  --output-on-failure

echo "M01 dependency and code-generation verification: PASS"
