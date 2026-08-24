"""Micro-averaged metrics that expose where a RAG run lost quality."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Iterable, Mapping

from .domain import EvaluationRecord


@dataclass(frozen=True, slots=True)
class MetricValue:
    value: float | None
    numerator: float
    denominator: float
    unit: str = "ratio"


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    case_count: int
    metrics: Mapping[str, MetricValue]
    latency_percentiles_ms: Mapping[str, float]
    stage_p95_latency_ms: Mapping[str, float]
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float
    error_counts: Mapping[str, int]


def evaluate_records(records: Iterable[EvaluationRecord]) -> EvaluationSummary:
    items = tuple(records)
    if not items:
        raise ValueError("evaluation requires at least one record")

    planner_required = [0, 0]
    unnecessary_routes = [0, 0]
    evidence_required = [0, 0]
    valid_evidence_deleted = [0, 0]
    conflicts_required = [0, 0]
    answer_correctness = [0, 0]
    answer_completeness = [0, 0]
    evidence_faithfulness = [0, 0]
    latencies: list[float] = []
    stage_latencies: dict[str, list[float]] = defaultdict(list)
    error_counts: Counter[str] = Counter()
    input_tokens = 0
    output_tokens = 0
    total_cost = 0.0

    for record in items:
        case = record.case
        actual = record.observation

        expected_route_set = set(case.expected_routes)
        actual_route_set = set(actual.actual_routes)
        planner_required[0] += len(expected_route_set & actual_route_set)
        planner_required[1] += len(expected_route_set)
        unnecessary_routes[0] += len(actual_route_set - expected_route_set)
        unnecessary_routes[1] += len(actual_route_set)

        expected_evidence_set = set(case.expected_evidence)
        retrieved_set = set(actual.retrieved_evidence)
        retained_set = set(actual.retained_evidence)
        retrieved_required = expected_evidence_set & retrieved_set
        evidence_required[0] += len(retrieved_required)
        evidence_required[1] += len(expected_evidence_set)
        valid_evidence_deleted[0] += len(retrieved_required - retained_set)
        valid_evidence_deleted[1] += len(retrieved_required)

        expected_conflict_set = set(case.expected_conflicts)
        conflicts_required[0] += len(
            expected_conflict_set & set(actual.detected_conflicts)
        )
        conflicts_required[1] += len(expected_conflict_set)

        answer_correctness[0] += sum(
            claim.correct for claim in actual.answer_claims
        )
        answer_correctness[1] += len(actual.answer_claims)
        correct_claim_ids = {
            claim.claim_id for claim in actual.answer_claims if claim.correct
        }
        expected_claim_set = set(case.expected_claims)
        answer_completeness[0] += len(correct_claim_ids & expected_claim_set)
        answer_completeness[1] += len(expected_claim_set)
        evidence_faithfulness[0] += sum(
            claim.supported_by_evidence for claim in actual.answer_claims
        )
        evidence_faithfulness[1] += len(actual.answer_claims)

        latencies.append(actual.latency_ms)
        for stage, duration in actual.stage_latency_ms.items():
            stage_latencies[stage].append(duration)
        input_tokens += actual.input_tokens
        output_tokens += actual.output_tokens
        total_cost += actual.estimated_cost_usd
        if actual.error_code is not None:
            error_counts[actual.error_code] += 1

    metrics = {
        "planner_required_recall": _ratio(*planner_required),
        "unnecessary_route_rate": _ratio(*unnecessary_routes),
        "required_evidence_recall": _ratio(*evidence_required),
        "valid_evidence_deletion_rate": _ratio(*valid_evidence_deleted),
        "conflict_detection_recall": _ratio(*conflicts_required),
        "answer_correctness": _ratio(*answer_correctness),
        "answer_completeness": _ratio(*answer_completeness),
        "evidence_faithfulness": _ratio(*evidence_faithfulness),
        "mean_estimated_cost_usd": MetricValue(
            value=total_cost / len(items),
            numerator=total_cost,
            denominator=len(items),
            unit="usd",
        ),
    }
    latency_percentiles = {
        name: _nearest_rank_percentile(latencies, percentile)
        for name, percentile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
    }
    return EvaluationSummary(
        case_count=len(items),
        metrics=MappingProxyType(metrics),
        latency_percentiles_ms=MappingProxyType(latency_percentiles),
        stage_p95_latency_ms=MappingProxyType(
            {
                stage: _nearest_rank_percentile(values, 0.95)
                for stage, values in sorted(stage_latencies.items())
            }
        ),
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_estimated_cost_usd=total_cost,
        error_counts=MappingProxyType(dict(sorted(error_counts.items()))),
    )


def _ratio(numerator: int, denominator: int) -> MetricValue:
    return MetricValue(
        value=numerator / denominator if denominator else None,
        numerator=numerator,
        denominator=denominator,
    )


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]
