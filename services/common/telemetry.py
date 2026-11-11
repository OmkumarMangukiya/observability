"""OpenTelemetry metrics and tracing shared by every service (M1.3, M1.4)."""

import os
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, make_asgi_app

from common.logging import configure_logging
from common.middleware import RequestMetricsMiddleware

SERVICE_NAME = "service.name"
DEPLOYMENT_ENVIRONMENT = "deployment.environment"

REQUEST_COUNT = "http.server.requests"
REQUEST_DURATION = "http.server.request.duration"

# OTel recommends second-based buckets for http.server.request.duration. The
# SDK default is millisecond-numbered, which would misplace second values.
_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
)


@dataclass(frozen=True)
class Instruments:
    """The metric instruments and registry owned by one service."""

    requests: Counter
    duration: Histogram
    registry: CollectorRegistry
    provider: MeterProvider


_instruments: dict[str, Instruments] = {}
_tracer_provider: TracerProvider | None = None
_http_clients_instrumented = False


def _resolve_service_name(service: str) -> str:
    """Prefer OTEL_SERVICE_NAME, falling back to the explicit argument."""
    return os.environ.get("OTEL_SERVICE_NAME") or service


def build_resource(service: str) -> Resource:
    """Return the OTel resource shared by metrics and traces."""
    return Resource.create(
        {
            SERVICE_NAME: _resolve_service_name(service),
            DEPLOYMENT_ENVIRONMENT: os.environ.get("ENVIRONMENT", "development"),
        }
    )


def _create_instruments(service: str) -> Instruments:
    """Create one service's meter provider, instruments and registry."""
    registry = CollectorRegistry()
    reader = PrometheusMetricReader(
        registry=registry,
        scope_info_enabled=False,
        resource_attribute_filter=lambda key: key == SERVICE_NAME,
    )
    provider = MeterProvider(metric_readers=[reader], resource=build_resource(service))
    meter = provider.get_meter("common.telemetry")

    return Instruments(
        requests=meter.create_counter(
            REQUEST_COUNT,
            unit="1",
            description="Number of HTTP requests.",
        ),
        duration=meter.create_histogram(
            REQUEST_DURATION,
            unit="s",
            description="Duration of HTTP requests.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        ),
        registry=registry,
        provider=provider,
    )


def get_instruments(service: str) -> Instruments:
    """Return a service's instruments, creating them once per process."""
    if service not in _instruments:
        _instruments[service] = _create_instruments(service)
    return _instruments[service]


def create_tracer_provider(service: str, endpoint: str | None = None) -> TracerProvider:
    """Build a tracer provider; add the OTLP exporter only when configured."""
    resolved = (
        endpoint if endpoint is not None else os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )
    provider = TracerProvider(resource=build_resource(service))
    if resolved.strip():
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{resolved.rstrip('/')}/v1/traces"))
        )
    return provider


def get_tracer_provider(service: str) -> TracerProvider:
    """Return the process-wide tracer provider, installing it once."""
    global _tracer_provider
    if _tracer_provider is None:
        _tracer_provider = create_tracer_provider(service)
        trace.set_tracer_provider(_tracer_provider)
    return _tracer_provider


def _instrument_http_clients() -> None:
    """Make httpx add traceparent to outgoing calls, once per process."""
    global _http_clients_instrumented
    if not _http_clients_instrumented:
        HTTPXClientInstrumentor().instrument()
        _http_clients_instrumented = True


def init_observability(app: FastAPI, service: str) -> None:
    """Wire logging, metrics and tracing into a service."""
    service = _resolve_service_name(service)
    configure_logging(service)
    instruments = get_instruments(service)
    get_tracer_provider(service)
    _instrument_http_clients()

    if getattr(app.state, "observability_initialized", False):
        return

    app.add_middleware(
        RequestMetricsMiddleware,
        requests=instruments.requests,
        duration=instruments.duration,
    )
    app.mount("/metrics", make_asgi_app(registry=instruments.registry))
    # Called last so its middleware wraps the routes and the server span is outermost.
    FastAPIInstrumentor.instrument_app(app)
    app.state.observability_initialized = True
