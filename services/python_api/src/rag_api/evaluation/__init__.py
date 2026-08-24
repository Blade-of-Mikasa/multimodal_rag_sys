"""Deterministic, stage-oriented evaluation for multimodal RAG runs."""

from .domain import (
    AnswerClaimAssessment,
    EvaluationCase,
    EvaluationObservation,
    EvaluationRecord,
)
from .io import load_records, write_run_artifacts
from .metrics import EvaluationSummary, MetricValue, evaluate_records

__all__ = [
    "AnswerClaimAssessment",
    "EvaluationCase",
    "EvaluationObservation",
    "EvaluationRecord",
    "EvaluationSummary",
    "MetricValue",
    "evaluate_records",
    "load_records",
    "write_run_artifacts",
]
