#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${repository_root}/.venv/bin/python"

if [[ ! -x "${python_bin}" ]]; then
  echo "Missing .venv. Run ./scripts/bootstrap_dependencies.sh first." >&2
  exit 1
fi

PYTHONPATH="${repository_root}/services/python_api/src" \
  "${python_bin}" -m unittest discover \
  -s "${repository_root}/services/python_api/tests" \
  -p "test_*.py" \
  -v

echo "M02 Python API verification: PASS"
