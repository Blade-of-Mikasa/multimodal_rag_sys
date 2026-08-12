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

    def test_core_settings_must_be_usable(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(core_grpc_target="  ", _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(core_grpc_timeout_seconds=0.01, _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(core_grpc_timeout_seconds=31, _env_file=None)

    def test_mysql_dsn_requires_asyncmy_and_is_secret(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(mysql_dsn="mysql+pymysql://rag:secret@db/rag", _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(mysql_dsn="mysql+asyncmy://rag:secret@db", _env_file=None)
        with self.assertRaises(ValidationError):
            Settings(mysql_dsn="not a database url", _env_file=None)

        settings = Settings(
            mysql_dsn="mysql+asyncmy://rag:secret@db/rag",
            _env_file=None,
        )

        self.assertNotIn("secret", repr(settings))
        self.assertEqual(
            "mysql+asyncmy://rag:secret@db/rag",
            settings.mysql_dsn.get_secret_value(),
        )


if __name__ == "__main__":
    unittest.main()
