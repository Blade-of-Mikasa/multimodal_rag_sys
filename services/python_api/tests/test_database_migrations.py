from __future__ import annotations

from io import StringIO
from pathlib import Path
import unittest

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG = REPOSITORY_ROOT / "services/python_api/alembic.ini"
EXPECTED_TABLES = {
    "access_control_lists",
    "access_control_entries",
    "assets",
    "asset_versions",
    "ingest_tasks",
    "conversations",
    "conversation_messages",
}


def alembic_config(output: StringIO | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG), output_buffer=output)
    return config


class DatabaseMigrationsTest(unittest.TestCase):
    def test_history_has_one_expected_head(self) -> None:
        scripts = ScriptDirectory.from_config(alembic_config())
        self.assertEqual(["20260813_0002"], scripts.get_heads())

    def test_upgrade_compiles_complete_mysql_ddl_offline(self) -> None:
        output = StringIO()
        command.upgrade(alembic_config(output), "head", sql=True)
        ddl = output.getvalue()

        for table_name in EXPECTED_TABLES:
            with self.subTest(table=table_name):
                self.assertIn(f"CREATE TABLE {table_name}", ddl)

        self.assertEqual(8, ddl.count("CREATE TABLE "))
        self.assertEqual(7, ddl.count("ENGINE=InnoDB"))
        self.assertEqual(7, ddl.count("CHARSET=utf8mb4"))
        self.assertIn("ON DELETE CASCADE", ddl)
        self.assertIn("ON DELETE RESTRICT", ddl)
        self.assertIn("CONSTRAINT uq_ingest_tasks_dedupe_key UNIQUE", ddl)
        self.assertIn("attributes_json JSON", ddl)
        self.assertIn("citations_json JSON", ddl)
        self.assertIn("ADD COLUMN lease_owner VARCHAR(128)", ddl)
        self.assertIn("ADD COLUMN lease_expires_at DATETIME(6)", ddl)
        self.assertIn("ADD COLUMN last_event_id CHAR(36)", ddl)
        self.assertIn("ADD COLUMN last_publish_error_message TEXT", ddl)
        self.assertIn("CREATE INDEX ix_ingest_tasks_outbox", ddl)

    def test_downgrade_compiles_all_table_drops_offline(self) -> None:
        output = StringIO()
        command.downgrade(
            alembic_config(output),
            "20260813_0002:base",
            sql=True,
        )
        ddl = output.getvalue()

        for table_name in EXPECTED_TABLES:
            with self.subTest(table=table_name):
                self.assertIn(f"DROP TABLE {table_name}", ddl)
        self.assertIn("DROP INDEX ix_ingest_tasks_outbox ON ingest_tasks", ddl)
        self.assertIn("DROP COLUMN last_event_id", ddl)
        self.assertIn("DROP COLUMN last_publish_error_message", ddl)


if __name__ == "__main__":
    unittest.main()
