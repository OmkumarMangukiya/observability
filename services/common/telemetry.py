"""OpenTelemetry metrics shared by every service (M1.3)."""

import os
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
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


def build_resource(service: str) -> Resource:
    """Return the OTel resource shared by metrics and traces."""
    return Resource.create(
        {
            SERVICE_NAME: service,
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


def init_observability(app: FastAPI, service: str) -> None:
    """Wire logging, request metrics and the /metrics endpoint into a service."""
    configure_logging(service)
    instruments = get_instruments(service)

    if getattr(app.state, "observability_initialized", False):
        return

    app.add_middleware(
        RequestMetricsMiddleware,
        requests=instruments.requests,
        duration=instruments.duration,
    )
    app.mount("/metrics", make_asgi_app(registry=instruments.registry))
    app.state.observability_initialized = True
