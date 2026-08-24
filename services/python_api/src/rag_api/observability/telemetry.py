"""Explicit OpenTelemetry spans and metrics for HTTP and streamed RAG queries."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import re
from time import monotonic_ns
from typing import Any, Callable, Iterator, Mapping

from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from rag_api.config import Settings


_INSTRUMENTATION_NAME = "multimodal_rag.observability"
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_KNOWN_FINISH_REASONS = {
    "completed",
    "incomplete",
    "insufficient_evidence",
    "length",
    "stop",
}


@dataclass(slots=True)
class _Instruments:
    http_duration: Any
    http_requests: Any
    query_duration: Any
    query_requests: Any
    stage_duration: Any
    failures: Any
    route_failures: Any
    tokens: Any
    estimated_cost: Any
    evidence_count: Any


class TelemetryRuntime:
    def __init__(
        self,
        *,
        enabled: bool,
        tracer_provider: trace.TracerProvider,
        meter_provider: metrics.MeterProvider,
        input_cost_per_million_tokens_usd: float,
        output_cost_per_million_tokens_usd: float,
        clock_ns: Callable[[], int] = monotonic_ns,
    ) -> None:
        self.enabled = enabled
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._tracer: Tracer = tracer_provider.get_tracer(_INSTRUMENTATION_NAME)
        meter: Meter = meter_provider.get_meter(_INSTRUMENTATION_NAME)
        self._instruments = _create_instruments(meter)
        self._input_cost = input_cost_per_million_tokens_usd
        self._output_cost = output_cost_per_million_tokens_usd
        self._clock_ns = clock_ns

    def start_http_request(
        self,
        *,
        request_id: str,
        method: str,
        headers: Mapping[str, str],
    ) -> "HttpRequestObservation":
        parent = propagate.extract(headers)
        span = self._tracer.start_span(
            f"HTTP {method}",
            context=parent,
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": method,
                "rag.request.id": request_id,
            },
        )
        return HttpRequestObservation(
            span=span,
            parent_context=trace.set_span_in_context(span, parent),
            method=method,
            instruments=self._instruments,
            clock_ns=self._clock_ns,
        )

    def start_query(
        self,
        *,
        request_id: str,
        retrieval_scope: str,
        modalities: tuple[str, ...],
        parent_context: Context | None = None,
    ) -> "QueryObservation":
        span = self._tracer.start_span(
            "rag.query",
            context=parent_context,
            kind=SpanKind.INTERNAL,
            attributes={
                "rag.request.id": request_id,
                "rag.retrieval.scope": retrieval_scope,
                "rag.modalities": ",".join(sorted(modalities)),
            },
        )
        return QueryObservation(
            span=span,
            span_context=trace.set_span_in_context(span, parent_context),
            tracer=self._tracer,
            instruments=self._instruments,
            retrieval_scope=retrieval_scope,
            input_cost_per_million_tokens_usd=self._input_cost,
            output_cost_per_million_tokens_usd=self._output_cost,
            clock_ns=self._clock_ns,
        )

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        trace_result = getattr(self._tracer_provider, "force_flush", lambda *_: True)(
            timeout_millis
        )
        metric_result = getattr(self._meter_provider, "force_flush", lambda *_: True)(
            timeout_millis
        )
        return bool(trace_result and metric_result)

    async def shutdown(self) -> None:
        await asyncio.to_thread(_shutdown_provider, self._meter_provider)
        await asyncio.to_thread(_shutdown_provider, self._tracer_provider)


class HttpRequestObservation:
    def __init__(
        self,
        *,
        span: Span,
        parent_context: Context,
        method: str,
        instruments: _Instruments,
        clock_ns: Callable[[], int],
    ) -> None:
        self.span = span
        self.context = parent_context
        self._method = method
        self._instruments = instruments
        self._clock_ns = clock_ns
        self._started_ns = clock_ns()
        self._ended = False

    @contextmanager
    def activate(self) -> Iterator[None]:
        with trace.use_span(self.span, end_on_exit=False):
            yield

    def finish(self, *, status_code: int, route: str) -> None:
        if self._ended:
            return
        self._ended = True
        duration_seconds = _seconds(self._clock_ns() - self._started_ns)
        route_name = route if route.startswith("/") else "unmatched"
        attributes = {
            "http.request.method": self._method,
            "http.route": route_name,
            "http.response.status_code": status_code,
        }
        self.span.update_name(f"{self._method} {route_name}")
        self.span.set_attributes(attributes)
        if status_code >= 500:
            self.span.set_status(Status(StatusCode.ERROR))
        self._instruments.http_duration.record(duration_seconds, attributes)
        self._instruments.http_requests.add(1, attributes)
        self.span.end()

    def fail(self, error: BaseException, *, route: str = "unmatched") -> None:
        if self._ended:
            return
        self.span.record_exception(error)
        self.span.set_status(Status(StatusCode.ERROR, type(error).__name__))
        self.finish(status_code=500, route=route)


class QueryObservation:
    def __init__(
        self,
        *,
        span: Span,
        span_context: Context,
        tracer: Tracer,
        instruments: _Instruments,
        retrieval_scope: str,
        input_cost_per_million_tokens_usd: float,
        output_cost_per_million_tokens_usd: float,
        clock_ns: Callable[[], int],
    ) -> None:
        self._span = span
        self._span_context = span_context
        self._tracer = tracer
        self._instruments = instruments
        self._retrieval_scope = retrieval_scope
        self._input_cost = input_cost_per_million_tokens_usd
        self._output_cost = output_cost_per_million_tokens_usd
        self._clock_ns = clock_ns
        self._started_ns = clock_ns()
        self._stages: dict[str, tuple[int, Span]] = {}
        self._input_tokens = 0
        self._output_tokens = 0
        self._ended = False

    @contextmanager
    def activate(self) -> Iterator[None]:
        with trace.use_span(self._span, end_on_exit=False):
            yield

    def observe(self, event: str, data: Mapping[str, Any]) -> None:
        if self._ended:
            return
        if event in {"planning", "retrieving"}:
            status = data.get("status")
            if status == "started":
                self._start_stage(event)
            elif status == "completed":
                self._finish_stage(event, outcome="completed")
            self._observe_usage(event, data.get("usage"))
        elif event == "sources":
            self._start_stage("generating")
            count = data.get("evidence_count")
            if isinstance(count, int) and count >= 0:
                self._instruments.evidence_count.record(
                    count, {"rag.retrieval.scope": self._retrieval_scope}
                )
                self._span.set_attribute("rag.evidence.count", count)
            route_errors = data.get("route_error_codes", ())
            if isinstance(route_errors, (list, tuple)):
                for code in route_errors:
                    if isinstance(code, str):
                        self._record_route_failure(code)
        elif event == "done":
            self._observe_usage("generation", data.get("usage"))
            self._finish_stage("generating", outcome="completed")
            finish_reason = data.get("finish_reason")
            self.finish(
                outcome=(
                    finish_reason
                    if isinstance(finish_reason, str)
                    else "completed"
                )
            )

    def finish(self, *, outcome: str) -> None:
        if self._ended:
            return
        self._ended = True
        normalized_outcome = _finish_outcome(outcome)
        for stage in tuple(self._stages):
            self._finish_stage(stage, outcome=normalized_outcome)
        attributes = {
            "rag.retrieval.scope": self._retrieval_scope,
            "rag.outcome": normalized_outcome,
        }
        duration = _seconds(self._clock_ns() - self._started_ns)
        self._instruments.query_duration.record(duration, attributes)
        self._instruments.query_requests.add(1, attributes)
        self._record_cost(attributes)
        self._span.set_attributes(
            {
                "rag.outcome": normalized_outcome,
                "rag.tokens.input": self._input_tokens,
                "rag.tokens.output": self._output_tokens,
            }
        )
        self._span.end()

    def fail(
        self,
        code: str,
        *,
        retryable: bool,
        error: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        if self._ended:
            return
        normalized_code = _error_code(code)
        attributes = {
            "error.type": normalized_code,
            "error.retryable": retryable,
            "rag.retrieval.scope": self._retrieval_scope,
        }
        self._span.set_attributes(attributes)
        if error is not None:
            self._span.record_exception(error)
        if not cancelled:
            self._instruments.failures.add(1, attributes)
            self._span.set_status(Status(StatusCode.ERROR, normalized_code))
        self.finish(outcome="cancelled" if cancelled else "error")

    def _start_stage(self, stage: str) -> None:
        if stage in self._stages:
            return
        self._stages[stage] = (
            self._clock_ns(),
            self._tracer.start_span(
                f"rag.query.{stage}", context=self._span_context
            ),
        )

    def _finish_stage(self, stage: str, *, outcome: str) -> None:
        state = self._stages.pop(stage, None)
        if state is None:
            return
        started_ns, span = state
        normalized_outcome = _finish_outcome(outcome)
        attributes = {
            "rag.stage": stage,
            "rag.stage.outcome": normalized_outcome,
            "rag.retrieval.scope": self._retrieval_scope,
        }
        self._instruments.stage_duration.record(
            _seconds(self._clock_ns() - started_ns), attributes
        )
        span.set_attributes(attributes)
        span.end()

    def _observe_usage(self, phase: str, value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        input_tokens = value.get("input_tokens")
        output_tokens = value.get("output_tokens")
        attributes = {"rag.model.phase": phase}
        if isinstance(input_tokens, int) and input_tokens >= 0:
            self._input_tokens += input_tokens
            self._instruments.tokens.add(
                input_tokens, {**attributes, "rag.token.type": "input"}
            )
        if isinstance(output_tokens, int) and output_tokens >= 0:
            self._output_tokens += output_tokens
            self._instruments.tokens.add(
                output_tokens, {**attributes, "rag.token.type": "output"}
            )

    def _record_route_failure(self, code: str) -> None:
        normalized = _error_code(code)
        attributes = {
            "error.type": normalized,
            "rag.retrieval.scope": self._retrieval_scope,
        }
        self._instruments.route_failures.add(1, attributes)
        self._span.add_event("rag.route.failure", attributes)

    def _record_cost(self, attributes: Mapping[str, Any]) -> None:
        cost = (
            self._input_tokens * self._input_cost
            + self._output_tokens * self._output_cost
        ) / 1_000_000
        self._instruments.estimated_cost.record(cost, attributes)
        self._span.set_attribute("rag.estimated_cost_usd", cost)


def build_telemetry(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
    clock_ns: Callable[[], int] = monotonic_ns,
) -> TelemetryRuntime:
    if not settings.telemetry_enabled:
        return TelemetryRuntime(
            enabled=False,
            tracer_provider=trace.NoOpTracerProvider(),
            meter_provider=metrics.NoOpMeterProvider(),
            input_cost_per_million_tokens_usd=(
                settings.chat_input_cost_per_million_tokens_usd
            ),
            output_cost_per_million_tokens_usd=(
                settings.chat_output_cost_per_million_tokens_usd
            ),
            clock_ns=clock_ns,
        )

    assert settings.otel_exporter_otlp_endpoint is not None
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
            "deployment.environment.name": settings.environment,
        }
    )
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
        shutdown_on_exit=False,
    )
    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        span_exporter = OTLPSpanExporter(
            endpoint=_signal_endpoint(
                settings.otel_exporter_otlp_endpoint, "traces"
            ),
            timeout=settings.otel_export_timeout_seconds,
        )
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=_signal_endpoint(
                    settings.otel_exporter_otlp_endpoint, "metrics"
                ),
                timeout=settings.otel_export_timeout_seconds,
            ),
            export_interval_millis=settings.otel_metric_export_interval_ms,
            export_timeout_millis=settings.otel_export_timeout_seconds * 1_000,
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    return TelemetryRuntime(
        enabled=True,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        input_cost_per_million_tokens_usd=(
            settings.chat_input_cost_per_million_tokens_usd
        ),
        output_cost_per_million_tokens_usd=(
            settings.chat_output_cost_per_million_tokens_usd
        ),
        clock_ns=clock_ns,
    )


def _create_instruments(meter: Meter) -> _Instruments:
    return _Instruments(
        http_duration=meter.create_histogram(
            "http.server.request.duration",
            unit="s",
            description="HTTP server request duration",
        ),
        http_requests=meter.create_counter(
            "http.server.requests",
            unit="{request}",
            description="Completed HTTP server requests",
        ),
        query_duration=meter.create_histogram(
            "rag.query.duration",
            unit="s",
            description="End-to-end streamed RAG query duration",
        ),
        query_requests=meter.create_counter(
            "rag.query.requests",
            unit="{query}",
            description="Completed streamed RAG queries",
        ),
        stage_duration=meter.create_histogram(
            "rag.query.stage.duration",
            unit="s",
            description="RAG query stage duration",
        ),
        failures=meter.create_counter(
            "rag.query.failures",
            unit="{failure}",
            description="Terminal RAG query failures",
        ),
        route_failures=meter.create_counter(
            "rag.retrieval.route.failures",
            unit="{failure}",
            description="Degraded retrieval route failures",
        ),
        tokens=meter.create_counter(
            "rag.model.tokens",
            unit="{token}",
            description="Planner and answer model token usage",
        ),
        estimated_cost=meter.create_histogram(
            "rag.query.estimated_cost",
            unit="USD",
            description="Estimated chat-model cost per RAG query",
        ),
        evidence_count=meter.create_histogram(
            "rag.query.evidence.count",
            unit="{evidence}",
            description="Evidence items retained for a RAG query",
        ),
    )


def _signal_endpoint(base: str, signal: str) -> str:
    return f"{base}/v1/{signal}"


def _seconds(duration_ns: int) -> float:
    return max(0, duration_ns) / 1_000_000_000


def _finish_outcome(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in _KNOWN_FINISH_REASONS or normalized in {"error", "cancelled"}:
        return normalized
    return "other"


def _error_code(value: str) -> str:
    normalized = value.strip().upper()
    return normalized if _SAFE_ERROR_CODE.fullmatch(normalized) else "UNKNOWN_ERROR"


def _shutdown_provider(provider: Any) -> None:
    shutdown = getattr(provider, "shutdown", None)
    if shutdown is not None:
        shutdown()
