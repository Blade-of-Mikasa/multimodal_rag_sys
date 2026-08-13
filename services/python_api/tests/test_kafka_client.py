from __future__ import annotations

import unittest

from rag_api.config import Settings
from rag_api.kafka.client import consumer_options, producer_options


class KafkaClientOptionsTest(unittest.TestCase):
    def test_producer_reliability_cannot_be_disabled_by_business_code(self) -> None:
        options = producer_options(Settings(_env_file=None))

        self.assertEqual("all", options["acks"])
        self.assertIs(True, options["enable_idempotence"])
        self.assertEqual(["127.0.0.1:9092"], options["bootstrap_servers"])

    def test_consumer_uses_manual_commits_and_read_committed_records(self) -> None:
        options = consumer_options(Settings(_env_file=None))

        self.assertIs(False, options["enable_auto_commit"])
        self.assertEqual("earliest", options["auto_offset_reset"])
        self.assertEqual("read_committed", options["isolation_level"])

    def test_sasl_secrets_are_unwrapped_only_for_client_construction(self) -> None:
        settings = Settings(
            kafka_security_protocol="SASL_SSL",
            kafka_sasl_username="rag-user",
            kafka_sasl_password="rag-secret",
            _env_file=None,
        )

        options = producer_options(settings)

        self.assertEqual("PLAIN", options["sasl_mechanism"])
        self.assertEqual("rag-user", options["sasl_plain_username"])
        self.assertEqual("rag-secret", options["sasl_plain_password"])


if __name__ == "__main__":
    unittest.main()
