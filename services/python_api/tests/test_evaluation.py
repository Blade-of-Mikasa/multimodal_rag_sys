from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rag_api.evaluation import (
    AnswerClaimAssessment,
    EvaluationCase,
    EvaluationObservation,
    EvaluationRecord,
    evaluate_records,
    load_records,
    write_run_artifacts,
)


def record(
    *,
    case_id: str,
    latency_ms: float,
    expected_routes: tuple[str, ...] = ("web:document",),
    actual_routes: tuple[str, ...] = ("web:document",),
    expected_evidence: tuple[str, ...] = ("nvidia-spec",),
    retrieved_evidence: tuple[str, ...] = ("nvidia-spec",),
    retained_evidence: tuple[str, ...] = ("nvidia-spec",),
    expected_conflicts: tuple[str, ...] = (),
    detected_conflicts: tuple[str, ...] = (),
    claims: tuple[AnswerClaimAssessment, ...] = (
        AnswerClaimAssessment("memory-32gb", True, True),
    ),
    error_code: str | None = None,
) -> EvaluationRecord:
    return EvaluationRecord(
        case=EvaluationCase(
            case_id=case_id,
            question="RTX 5090 有多少显存？",
            expected_routes=expected_routes,
            expected_evidence=expected_evidence,
            expected_claims=("memory-32gb",),
            expected_conflicts=expected_conflicts,
        ),
        observation=EvaluationObservation(
            case_id=case_id,
            actual_routes=actual_routes,
            retrieved_evidence=retrieved_evidence,
            retained_evidence=retained_evidence,
            detected_conflicts=detected_conflicts,
            answer_claims=claims,
            latency_ms=latency_ms,
            input_tokens=100,
            output_tokens=20,
            estimated_cost_usd=0.001,
            stage_latency_ms={"planning": 10, "retrieving": latency_ms - 10},
            error_code=error_code,
        ),
    )


class EvaluationMetricsTest(unittest.TestCase):
    def test_computes_micro_averages_percentiles_cost_and_errors(self) -> None:
        summary = evaluate_records(
            (
                record(case_id="case-1", latency_ms=100),
                record(
                    case_id="case-2",
                    latency_ms=900,
                    actual_routes=("local:document", "web:document"),
                    retrieved_evidence=("nvidia-spec", "blog"),
                    retained_evidence=("blog",),
                    expected_conflicts=("memory-version",),
                    claims=(
                        AnswerClaimAssessment("memory-32gb", True, True),
                        AnswerClaimAssessment("release-2024", False, False),
                    ),
                    error_code="WEB_SEARCH_ROUTE_FAILED",
                ),
            )
        )

        self.assertEqual(2, summary.case_count)
        self.assertEqual(
            1.0, summary.metrics["planner_required_recall"].value
        )
        self.assertEqual(1 / 3, summary.metrics["unnecessary_route_rate"].value)
        self.assertEqual(
            0.5, summary.metrics["valid_evidence_deletion_rate"].value
        )
        self.assertEqual(
            2 / 3, summary.metrics["answer_correctness"].value
        )
        self.assertEqual(
            2 / 3, summary.metrics["evidence_faithfulness"].value
        )
        self.assertEqual(0.0, summary.metrics["conflict_detection_recall"].value)
        self.assertEqual(900, summary.latency_percentiles_ms["p95"])
        self.assertEqual(890, summary.stage_p95_latency_ms["retrieving"])
        self.assertEqual(200, summary.total_input_tokens)
        self.assertAlmostEqual(0.002, summary.total_estimated_cost_usd)
        self.assertEqual({"WEB_SEARCH_ROUTE_FAILED": 1}, summary.error_counts)

    def test_uses_not_applicable_instead_of_inventing_a_perfect_score(self) -> None:
        summary = evaluate_records(
            (
                record(
                    case_id="refusal",
                    latency_ms=20,
                    expected_routes=(),
                    actual_routes=(),
                    expected_evidence=(),
                    retrieved_evidence=(),
                    retained_evidence=(),
                    claims=(),
                ),
            )
        )

        self.assertIsNone(summary.metrics["planner_required_recall"].value)
        self.assertIsNone(summary.metrics["unnecessary_route_rate"].value)
        self.assertIsNone(summary.metrics["answer_correctness"].value)

    def test_rejects_retained_evidence_that_was_not_retrieved(self) -> None:
        with self.assertRaisesRegex(ValueError, "subset"):
            EvaluationObservation(
                case_id="invalid",
                actual_routes=(),
                retrieved_evidence=(),
                retained_evidence=("invented",),
                detected_conflicts=(),
                answer_claims=(),
                latency_ms=1,
            )

        with self.assertRaisesRegex(TypeError, "boolean labels"):
            AnswerClaimAssessment("claim", "yes", True)  # type: ignore[arg-type]


class EvaluationIoTest(unittest.TestCase):
    def test_loads_strict_jsonl_and_writes_reviewable_artifacts(self) -> None:
        payload = {
            "case": {
                "case_id": "case-jsonl",
                "question": "What is the memory size?",
                "expected_routes": ["web:document"],
                "expected_evidence": ["nvidia-spec"],
                "expected_claims": ["memory-32gb"],
                "tags": ["web", "fact"],
            },
            "observation": {
                "case_id": "case-jsonl",
                "actual_routes": ["web:document"],
                "retrieved_evidence": ["nvidia-spec"],
                "retained_evidence": ["nvidia-spec"],
                "detected_conflicts": [],
                "answer_claims": [
                    {
                        "claim_id": "memory-32gb",
                        "correct": True,
                        "supported_by_evidence": True,
                    }
                ],
                "latency_ms": 120.5,
            },
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "run.jsonl"
            input_path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
            )

            summary = evaluate_records(load_records(input_path))
            summary_path, report_path = write_run_artifacts(
                root / "report",
                run_id="m13-test",
                summary=summary,
            )

            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
            self.assertEqual("rag-evaluation.v1", summary_payload["schema_version"])
            self.assertIn("召回规划覆盖率", report)
            self.assertIn("不计算统一 RAG 总分", report)

    def test_rejects_unknown_fields_with_line_number(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.jsonl"
            path.write_text(
                json.dumps({"case": {}, "observation": {}, "typo": True}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "line 1.*unexpected fields"):
                load_records(path)
