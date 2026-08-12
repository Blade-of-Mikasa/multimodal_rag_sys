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
  -p "test_database*.py" \
  -v
"${repository_root}/scripts/verify_python_api.sh"
"${repository_root}/.venv/bin/alembic" \
  -c "${repository_root}/services/python_api/alembic.ini" \
  upgrade head --sql >/dev/null

echo "M04 MySQL metadata and migration verification: PASS"
