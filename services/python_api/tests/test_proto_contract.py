from __future__ import annotations

import pathlib
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PROTO_FILE = REPOSITORY_ROOT / "proto" / "rag_core.proto"


class ProtoContractTest(unittest.TestCase):
    def test_v1_contract_has_required_boundaries(self) -> None:
        contract = PROTO_FILE.read_text(encoding="utf-8")
        required_fragments = (
            'package multimodal.rag.v1;',
            "service RagCoreService",
            "rpc ExecutePlan",
            "service IndexCoreService",
            "rpc IndexAsset",
            "message Evidence",
            "repeated Evidence external_evidence",
            "message EvidenceDecision",
            "context_token_budget",
            "token_count_method",
            "message RouteError",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, contract)

    def test_contract_braces_are_balanced(self) -> None:
        contract = PROTO_FILE.read_text(encoding="utf-8")
        self.assertEqual(contract.count("{"), contract.count("}"))


if __name__ == "__main__":
    unittest.main()
