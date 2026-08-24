from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from rag_api.config import Settings
from rag_api.app import create_app
from rag_api.core_client import CoreHealth
from rag_api.generation import AnswerUpdate
from rag_api.observability import build_telemetry


class ObservabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.span_exporter = InMemorySpanExporter()
        self.metric_reader = InMemoryMetricReader()
        self.runtime = build_telemetry(
            Settings(
                environment="test",
                telemetry_enabled=True,
                otel_exporter_otlp_endpoint="http://collector:4318/otel",
                chat_input_cost_per_million_tokens_usd=2,
                chat_output_cost_per_million_tokens_usd=8,
                _env_file=None,
            ),
            span_exporter=self.span_exporter,
            metric_reader=self.metric_reader,
        )

    def tearDown(self) -> None:
        asyncio.run(self.runtime.shutdown())

    def test_records_http_and_stage_spans_metrics_tokens_and_cost(self) -> None:
        http = self.runtime.start_http_request(
            request_id="request-secret-high-cardinality",
            method="POST",
            headers={},
        )
        with http.activate():
            query = self.runtime.start_query(
                request_id="request-secret-high-cardinality",
                retrieval_scope="hybrid",
                modalities=("document", "video"),
                parent_context=http.context,
            )
            query.observe("planning", {"status": "started"})
            query.observe(
                "planning",
                {
                    "status": "completed",
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                },
            )
            query.observe("retrieving", {"status": "started"})
            query.observe("retrieving", {"status": "completed"})
            query.observe(
                "sources",
                {
                    "evidence_count": 3,
                    "route_error_codes": ["WEB_SEARCH_ROUTE_FAILED"],
                },
            )
            query.observe(
                "done",
                {
                    "finish_reason": "completed",
                    "usage": {"input_tokens": 200, "output_tokens": 50},
                },
            )
        http.finish(status_code=200, route="/api/v1/queries/stream")

        self.assertTrue(self.runtime.force_flush())
        spans = self.span_exporter.get_finished_spans()
        span_names = {span.name for span in spans}
        self.assertIn("POST /api/v1/queries/stream", span_names)
        self.assertIn("rag.query", span_names)
        self.assertIn("rag.query.planning", span_names)
        self.assertIn("rag.query.retrieving", span_names)
        self.assertIn("rag.query.generating", span_names)

        query_span = next(span for span in spans if span.name == "rag.query")
        self.assertEqual("completed", query_span.attributes["rag.outcome"])
        self.assertEqual(300, query_span.attributes["rag.tokens.input"])
        self.assertEqual(60, query_span.attributes["rag.tokens.output"])
        self.assertAlmostEqual(
            0.00108, query_span.attributes["rag.estimated_cost_usd"]
        )
        self.assertEqual(1, len(query_span.events))

        metrics = _metric_points(self.metric_reader)
        self.assertIn("rag.query.duration", metrics)
        self.assertIn("rag.query.stage.duration", metrics)
        self.assertIn("rag.query.estimated_cost", metrics)
        self.assertIn("rag.retrieval.route.failures", metrics)
        for points in metrics.values():
            for point in points:
                self.assertNotIn("rag.request.id", point.attributes)

    def test_normalizes_unbounded_error_and_finish_values(self) -> None:
        query = self.runtime.start_query(
            request_id="request-2",
            retrieval_scope="web",
            modalities=("document",),
        )
        query.observe("planning", {"status": "started"})
        query.fail("provider says user=123", retryable=True)

        self.runtime.force_flush()
        span = next(
            item
            for item in self.span_exporter.get_finished_spans()
            if item.name == "rag.query"
        )
        self.assertEqual("UNKNOWN_ERROR", span.attributes["error.type"])
        self.assertEqual("error", span.attributes["rag.outcome"])


class _CoreClient:
    async def health(self):
        return CoreHealth("multimodal-rag-core", "test", True)

    async def close(self):
        pass


class _AnswerService:
    async def stream(self, **_arguments):
        yield AnswerUpdate("planning", {"status": "started"})
        yield AnswerUpdate(
            "planning",
            {
                "status": "completed",
                "routes": [],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )
        yield AnswerUpdate("retrieving", {"status": "started"})
        yield AnswerUpdate(
            "retrieving", {"status": "completed", "evidence_count": 0}
        )
        yield AnswerUpdate(
            "sources",
            {
                "evidence_count": 0,
                "citations": [],
                "conflicts": [],
                "route_error_codes": [],
            },
        )
        yield AnswerUpdate(
            "done",
            {
                "answer": "insufficient",
                "finish_reason": "insufficient_evidence",
            },
        )


class ApiObservabilityTest(unittest.TestCase):
    def test_incoming_trace_context_is_parent_of_streamed_query(self) -> None:
        span_exporter = InMemorySpanExporter()
        metric_reader = InMemoryMetricReader()
        settings = Settings(
            environment="test",
            telemetry_enabled=True,
            otel_exporter_otlp_endpoint="http://collector:4318",
            _env_file=None,
        )
        telemetry = build_telemetry(
            settings,
            span_exporter=span_exporter,
            metric_reader=metric_reader,
        )
        trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
        parent_span_id = "00f067aa0ba902b7"
        with TestClient(
            create_app(
                settings,
                _CoreClient(),
                answer_service=_AnswerService(),
                telemetry=telemetry,
            )
        ) as client:
            response = client.post(
                "/api/v1/queries/stream",
                json={"query": "question"},
                headers={
                    "X-Tenant-ID": "tenant-1",
                    "X-User-ID": "user-1",
                    "traceparent": f"00-{trace_id}-{parent_span_id}-01",
                },
            )

        self.assertEqual(200, response.status_code)
        spans = span_exporter.get_finished_spans()
        http_span = next(span for span in spans if span.kind is SpanKind.SERVER)
        query_span = next(span for span in spans if span.name == "rag.query")
        self.assertEqual(
            "/api/v1/queries/stream", http_span.attributes["http.route"]
        )
        self.assertEqual(int(trace_id, 16), http_span.context.trace_id)
        self.assertEqual(int(parent_span_id, 16), http_span.parent.span_id)
        self.assertEqual(http_span.context.trace_id, query_span.context.trace_id)
        self.assertEqual(http_span.context.span_id, query_span.parent.span_id)
        self.assertEqual(
            "insufficient_evidence", query_span.attributes["rag.outcome"]
        )


def _metric_points(reader: InMemoryMetricReader) -> dict[str, tuple]:
    data = reader.get_metrics_data()
    assert data is not None
    result = {}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                result[metric.name] = tuple(metric.data.data_points)
    return result
