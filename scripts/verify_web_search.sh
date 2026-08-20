#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_path="${repository_root}/services/python_api/src:${repository_root}/build/generated/python"

PYTHONPATH="${python_path}" "${repository_root}/.venv/bin/python" -m unittest -v \
  "${repository_root}/services/python_api/tests/test_bing_search_provider.py" \
  "${repository_root}/services/python_api/tests/test_web_fetcher.py" \
  "${repository_root}/services/python_api/tests/test_web_extractor.py" \
  "${repository_root}/services/python_api/tests/test_web_search_service.py" \
  "${repository_root}/services/python_api/tests/test_web_runtime.py" \
  "${repository_root}/services/python_api/tests/test_config.py"

PYTHONPATH="${python_path}" "${repository_root}/.venv/bin/python" -m unittest discover \
  -s "${repository_root}/services/python_api/tests" -v

"${repository_root}/.venv/bin/python" -m pip check
git -C "${repository_root}" diff --check

echo "M10 web search and extraction verification: PASS"
