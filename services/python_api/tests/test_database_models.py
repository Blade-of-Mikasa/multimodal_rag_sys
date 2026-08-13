from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from rag_api.config import Settings
from rag_api.db import Base
from rag_api.db.session import create_database_engine, create_session_factory


EXPECTED_TABLES = {
    "access_control_lists",
    "access_control_entries",
    "assets",
    "asset_versions",
    "ingest_tasks",
    "conversations",
    "conversation_messages",
}


class DatabaseModelsTest(unittest.TestCase):
    def test_schema_contains_only_the_m04_tables(self) -> None:
        self.assertEqual(EXPECTED_TABLES, set(Base.metadata.tables))

    def test_all_tables_use_the_mysql_8_storage_baseline(self) -> None:
        for table in Base.metadata.tables.values():
            with self.subTest(table=table.name):
                self.assertEqual("InnoDB", table.dialect_options["mysql"]["engine"])
                self.assertEqual("utf8mb4", table.dialect_options["mysql"]["charset"])
                self.assertEqual(
                    "utf8mb4_0900_ai_ci",
                    table.dialect_options["mysql"]["collate"],
                )
                ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
                self.assertIn("ENGINE=InnoDB", ddl)
                self.assertIn("CHARSET=utf8mb4", ddl)

    def test_foreign_keys_have_explicit_delete_semantics(self) -> None:
        expected = {
            ("access_control_entries", "acl_id"): "CASCADE",
            ("assets", "acl_id"): "RESTRICT",
            ("asset_versions", "asset_id"): "CASCADE",
            ("ingest_tasks", "asset_id"): "CASCADE",
            ("ingest_tasks", "asset_version_id"): "CASCADE",
            ("conversations", "acl_id"): "RESTRICT",
            ("conversation_messages", "conversation_id"): "CASCADE",
        }

        actual: dict[tuple[str, str], str | None] = {}
        for table in Base.metadata.tables.values():
            for foreign_key in table.foreign_keys:
                actual[(table.name, foreign_key.parent.name)] = foreign_key.ondelete

        self.assertEqual(expected, actual)

    def test_idempotency_and_version_constraints_are_present(self) -> None:
        task_uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in Base.metadata.tables["ingest_tasks"].constraints
            if isinstance(constraint, UniqueConstraint)
        }
        version_uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in Base.metadata.tables["asset_versions"].constraints
            if isinstance(constraint, UniqueConstraint)
        }
        named_checks = {
            constraint.name
            for table in Base.metadata.tables.values()
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertIn(("dedupe_key",), task_uniques)
        self.assertIn(("asset_id", "version_number"), version_uniques)
        self.assertIn("ck_ingest_tasks_attempts", named_checks)
        self.assertIn("ck_conversation_messages_role", named_checks)
        ingest_table = Base.metadata.tables["ingest_tasks"]
        self.assertIn(
            "ix_ingest_tasks_outbox",
            {index.name for index in ingest_table.indexes},
        )
        self.assertIn("lease_owner", ingest_table.columns)
        self.assertIn("lease_expires_at", ingest_table.columns)
        self.assertIn("last_event_id", ingest_table.columns)
        self.assertIn("last_publish_error_message", ingest_table.columns)

    def test_async_engine_and_session_factory_do_not_connect_eagerly(self) -> None:
        settings = Settings(
            mysql_dsn="mysql+asyncmy://rag:secret@db.example/rag",
            _env_file=None,
        )
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)

        self.assertEqual("mysql", engine.url.get_backend_name())
        self.assertEqual("asyncmy", engine.url.get_driver_name())
        self.assertNotIn("secret", str(engine.url))
        self.assertFalse(session_factory.kw["expire_on_commit"])

        asyncio.run(engine.dispose())


if __name__ == "__main__":
    unittest.main()
