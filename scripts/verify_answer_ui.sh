#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_path="${repository_root}/services/python_api/src:${repository_root}/build/generated/python"

PYTHONPATH="${python_path}" "${repository_root}/.venv/bin/python" -m unittest -v \
  "${repository_root}/services/python_api/tests/test_chat_model.py" \
  "${repository_root}/services/python_api/tests/test_query_planner.py" \
  "${repository_root}/services/python_api/tests/test_answer_service.py" \
  "${repository_root}/services/python_api/tests/test_api.py" \
  "${repository_root}/services/python_api/tests/test_config.py" \
  "${repository_root}/services/python_api/tests/test_web_evidence.py"

npm --prefix "${repository_root}/services/web_ui" test
npm --prefix "${repository_root}/services/web_ui" exec -- \
  tsc --noEmit --project "${repository_root}/services/web_ui/tsconfig.json"

if [[ "${RAG_VERIFY_FRONTEND_BUILD:-0}" == "1" ]]; then
  npm --prefix "${repository_root}/services/web_ui" run build
else
  echo "Frontend production build skipped; set RAG_VERIFY_FRONTEND_BUILD=1 for the manual compile stage."
fi

git -C "${repository_root}" diff --check

echo "M12 answer generation and UI verification: PASS"
