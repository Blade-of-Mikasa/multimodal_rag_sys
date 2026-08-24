"""Strict input contracts for replayable, claim-level RAG evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from types import MappingProxyType
from typing import Mapping


_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_LABEL_BYTES = 4_096


def _bounded_text(value: str, name: str, *, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} must contain bounded non-blank text")
    return normalized


def _unique_labels(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(
        _bounded_text(value, name, max_bytes=_MAX_LABEL_BYTES) for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must contain unique values")
    return normalized


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    question: str
    expected_routes: tuple[str, ...]
    expected_evidence: tuple[str, ...]
    expected_claims: tuple[str, ...]
    expected_conflicts: tuple[str, ...] = ()
    reference_answer: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not _SAFE_CASE_ID.fullmatch(
            self.case_id
        ):
            raise ValueError("case_id must be a safe identifier")
        object.__setattr__(
            self,
            "question",
            _bounded_text(self.question, "question", max_bytes=65_536),
        )
        for name in (
            "expected_routes",
            "expected_evidence",
            "expected_claims",
            "expected_conflicts",
            "tags",
        ):
            object.__setattr__(
                self,
                name,
                _unique_labels(getattr(self, name), name),
            )
        if self.reference_answer is not None:
            object.__setattr__(
                self,
                "reference_answer",
                _bounded_text(
                    self.reference_answer,
                    "reference_answer",
                    max_bytes=1_000_000,
                ),
            )


@dataclass(frozen=True, slots=True)
class AnswerClaimAssessment:
    claim_id: str
    correct: bool
    supported_by_evidence: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "claim_id",
            _bounded_text(self.claim_id, "claim_id", max_bytes=_MAX_LABEL_BYTES),
        )
        if not isinstance(self.correct, bool) or not isinstance(
            self.supported_by_evidence, bool
        ):
            raise TypeError("claim assessments must use boolean labels")


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    case_id: str
    actual_routes: tuple[str, ...]
    retrieved_evidence: tuple[str, ...]
    retained_evidence: tuple[str, ...]
    detected_conflicts: tuple[str, ...]
    answer_claims: tuple[AnswerClaimAssessment, ...]
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    stage_latency_ms: Mapping[str, float] = field(default_factory=dict)
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not _SAFE_CASE_ID.fullmatch(
            self.case_id
        ):
            raise ValueError("case_id must be a safe identifier")
        for name in (
            "actual_routes",
            "retrieved_evidence",
            "retained_evidence",
            "detected_conflicts",
        ):
            object.__setattr__(
                self,
                name,
                _unique_labels(getattr(self, name), name),
            )
        if not set(self.retained_evidence).issubset(self.retrieved_evidence):
            raise ValueError("retained_evidence must be a subset of retrieved_evidence")
        if any(
            not isinstance(claim, AnswerClaimAssessment)
            for claim in self.answer_claims
        ):
            raise TypeError("answer_claims must contain claim assessments")
        claim_ids = tuple(claim.claim_id for claim in self.answer_claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("answer claim IDs must be unique")
        _non_negative_finite(self.latency_ms, "latency_ms")
        _non_negative_finite(self.estimated_cost_usd, "estimated_cost_usd")
        if (
            not isinstance(self.input_tokens, int)
            or isinstance(self.input_tokens, bool)
            or not isinstance(self.output_tokens, int)
            or isinstance(self.output_tokens, bool)
            or self.input_tokens < 0
            or self.output_tokens < 0
        ):
            raise ValueError("token counts must be non-negative")
        if not isinstance(self.stage_latency_ms, Mapping):
            raise TypeError("stage_latency_ms must be an object")
        normalized_stages: dict[str, float] = {}
        for name, duration in self.stage_latency_ms.items():
            normalized_name = _bounded_text(
                name, "stage name", max_bytes=128
            )
            if normalized_name in normalized_stages:
                raise ValueError("stage names must be unique after normalization")
            _non_negative_finite(duration, f"stage {normalized_name}")
            normalized_stages[normalized_name] = duration
        object.__setattr__(
            self, "stage_latency_ms", MappingProxyType(normalized_stages)
        )
        if self.error_code is not None:
            object.__setattr__(
                self,
                "error_code",
                _bounded_text(self.error_code, "error_code", max_bytes=128),
            )


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    case: EvaluationCase
    observation: EvaluationObservation

    def __post_init__(self) -> None:
        if self.case.case_id != self.observation.case_id:
            raise ValueError("case and observation IDs must match")


def _non_negative_finite(value: float, name: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be non-negative and finite")
