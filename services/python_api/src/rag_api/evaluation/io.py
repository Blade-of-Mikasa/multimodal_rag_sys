"""JSONL replay input and atomic JSON/Markdown report output."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from .domain import (
    AnswerClaimAssessment,
    EvaluationCase,
    EvaluationObservation,
    EvaluationRecord,
)
from .metrics import EvaluationSummary
from .report import render_markdown


def load_records(path: Path) -> tuple[EvaluationRecord, ...]:
    records: list[EvaluationRecord] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
                records.append(_record(payload))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"invalid evaluation record at line {line_number}: {error}"
                ) from error
    if not records:
        raise ValueError("evaluation JSONL must contain at least one record")
    return tuple(records)


def write_run_artifacts(
    output_directory: Path,
    *,
    run_id: str,
    summary: EvaluationSummary,
    baseline: dict[str, Any] | None = None,
    source_sha256: str | None = None,
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = _summary_payload(
        run_id=run_id,
        generated_at=generated_at,
        summary=summary,
        source_sha256=source_sha256,
        baseline_run_id=(
            baseline.get("run_id") if isinstance(baseline, dict) else None
        ),
    )
    summary_path = output_directory / "summary.json"
    report_path = output_directory / "report.md"
    _atomic_write(
        summary_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(
        report_path,
        render_markdown(payload, baseline=baseline),
    )
    return summary_path, report_path


def _record(payload: Any) -> EvaluationRecord:
    row = _object(payload, "record")
    _keys(row, required={"case", "observation"})
    case_payload = _object(row["case"], "case")
    observation_payload = _object(row["observation"], "observation")
    _keys(
        case_payload,
        required={
            "case_id",
            "question",
            "expected_routes",
            "expected_evidence",
            "expected_claims",
        },
        optional={"expected_conflicts", "reference_answer", "tags"},
    )
    _keys(
        observation_payload,
        required={
            "case_id",
            "actual_routes",
            "retrieved_evidence",
            "retained_evidence",
            "detected_conflicts",
            "answer_claims",
            "latency_ms",
        },
        optional={
            "input_tokens",
            "output_tokens",
            "estimated_cost_usd",
            "stage_latency_ms",
            "error_code",
        },
    )
    claims_payload = observation_payload["answer_claims"]
    if not isinstance(claims_payload, list):
        raise TypeError("answer_claims must be an array")
    claims: list[AnswerClaimAssessment] = []
    for claim_payload in claims_payload:
        claim = _object(claim_payload, "answer claim")
        _keys(
            claim,
            required={"claim_id", "correct", "supported_by_evidence"},
        )
        claims.append(AnswerClaimAssessment(**claim))
    return EvaluationRecord(
        case=EvaluationCase(
            case_id=case_payload["case_id"],
            question=case_payload["question"],
            expected_routes=_strings(case_payload["expected_routes"]),
            expected_evidence=_strings(case_payload["expected_evidence"]),
            expected_claims=_strings(case_payload["expected_claims"]),
            expected_conflicts=_strings(
                case_payload.get("expected_conflicts", [])
            ),
            reference_answer=case_payload.get("reference_answer"),
            tags=_strings(case_payload.get("tags", [])),
        ),
        observation=EvaluationObservation(
            case_id=observation_payload["case_id"],
            actual_routes=_strings(observation_payload["actual_routes"]),
            retrieved_evidence=_strings(
                observation_payload["retrieved_evidence"]
            ),
            retained_evidence=_strings(observation_payload["retained_evidence"]),
            detected_conflicts=_strings(
                observation_payload["detected_conflicts"]
            ),
            answer_claims=tuple(claims),
            latency_ms=observation_payload["latency_ms"],
            input_tokens=observation_payload.get("input_tokens", 0),
            output_tokens=observation_payload.get("output_tokens", 0),
            estimated_cost_usd=observation_payload.get(
                "estimated_cost_usd", 0.0
            ),
            stage_latency_ms=observation_payload.get("stage_latency_ms", {}),
            error_code=observation_payload.get("error_code"),
        ),
    )


def _summary_payload(
    *,
    run_id: str,
    generated_at: str,
    summary: EvaluationSummary,
    source_sha256: str | None,
    baseline_run_id: Any,
) -> dict[str, Any]:
    return {
        "schema_version": "rag-evaluation.v1",
        "run_id": run_id,
        "generated_at": generated_at,
        "provenance": {
            "source_sha256": source_sha256,
            "baseline_run_id": (
                baseline_run_id if isinstance(baseline_run_id, str) else None
            ),
        },
        "case_count": summary.case_count,
        "metrics": {
            name: asdict(value) for name, value in summary.metrics.items()
        },
        "latency_percentiles_ms": dict(summary.latency_percentiles_ms),
        "stage_p95_latency_ms": dict(summary.stage_p95_latency_ms),
        "usage": {
            "input_tokens": summary.total_input_tokens,
            "output_tokens": summary.total_output_tokens,
            "estimated_cost_usd": summary.total_estimated_cost_usd,
        },
        "error_counts": dict(summary.error_counts),
    }


def _atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{name} must be an object with string keys")
    return value


def _keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    missing = required - value.keys()
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    unexpected = value.keys() - required - (optional or set())
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(sorted(unexpected))}")


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError("expected an array of strings")
    return tuple(value)
