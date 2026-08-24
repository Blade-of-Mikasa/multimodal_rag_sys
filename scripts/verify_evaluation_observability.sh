#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_path="${repository_root}/services/python_api/src:${repository_root}/build/generated/python"
temporary_dir="$(mktemp -d)"
trap 'rm -rf "${temporary_dir}"' EXIT

PYTHONPATH="${python_path}" "${repository_root}/.venv/bin/python" -m unittest -v \
  "${repository_root}/services/python_api/tests/test_evaluation.py" \
  "${repository_root}/services/python_api/tests/test_observability.py" \
  "${repository_root}/services/python_api/tests/test_config.py" \
  "${repository_root}/services/python_api/tests/test_query_planner.py" \
  "${repository_root}/services/python_api/tests/test_answer_service.py" \
  "${repository_root}/services/python_api/tests/test_api.py"

PYTHONPATH="${python_path}" "${repository_root}/.venv/bin/python" -m \
  rag_api.evaluation.cli \
  --input "${repository_root}/evaluation/fixtures/m13_smoke_replay.jsonl" \
  --output-dir "${temporary_dir}/report" \
  --run-id "m13-smoke"

test -s "${temporary_dir}/report/summary.json"
test -s "${temporary_dir}/report/report.md"
"${repository_root}/.venv/bin/python" -m pip check
git -C "${repository_root}" diff --check

echo "M13 evaluation and observability verification: PASS"
