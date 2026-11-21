"""OpenTelemetry tracing.

Spans: HTTP requests (FastAPI instrumentation), workflow nodes, LLM calls, retrieval.
Export over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set (compose `observability`
profile → Jaeger); otherwise spans are created but not exported, at negligible cost.
trace_id/span_id are added to every log line, so logs and traces can be correlated.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.settings import Settings

_configured = False


def configure_tracing(settings: Settings, service_name: str) -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "deployment.environment": settings.env})
    )
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces")
            )
        )
    trace.set_tracer_provider(provider)
    _configured = True


def add_trace_ids(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = f"{ctx.trace_id:032x}"
        event_dict["span_id"] = f"{ctx.span_id:016x}"
    return event_dict
