from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from rag_api.config import Settings


class SettingsTest(unittest.TestCase):
    def test_environment_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {"RAG_ENVIRONMENT": "test", "RAG_API_PREFIX": "/internal/v1"},
            clear=False,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual("test", settings.environment)
        self.assertEqual("/internal/v1", settings.api_prefix)

    def test_api_prefix_must_be_canonical(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(api_prefix="api/v1", _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(api_prefix="/api/v1/", _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(api_prefix="/", _env_file=None)


if __name__ == "__main__":
    unittest.main()
