"""Tests for tracing wiring (M1.4)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from common import telemetry
from common.logging import get_logger
from common.telemetry import init_observability


def test_handler_sees_valid_span_and_log_matches(capsys) -> None:
    """The handler runs inside a valid server span, and the log carries its trace ID."""
    app = FastAPI()
    init_observability(app, "tracing-handler")
    logger = get_logger("tests.test_tracing.handler")

    @app.get("/orders")
    def list_orders() -> dict[str, object]:
        span_context = trace.get_current_span().get_span_context()
        logger.info("handled")
        return {
            "trace_id": f"{span_context.trace_id:032x}",
            "valid": span_context.is_valid,
        }

    response = TestClient(app).get("/orders")
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    handled = next(event for event in events if event["event"] == "handled")

    body = response.json()
    assert body["valid"] is True
    assert handled["trace_id"] == body["trace_id"]
    assert handled["span_id"] is not None


def test_httpx_call_inside_span_carries_traceparent() -> None:
    """An outgoing httpx request inside a span injects that span's traceparent."""
    app = FastAPI()
    init_observability(app, "tracing-httpx")
    received: dict[str, str | None] = {}

    class Recorder(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received["traceparent"] = self.headers.get("traceparent")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            """Silence the default request log."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tracer = trace.get_tracer("tests.test_tracing")
        with tracer.start_as_current_span("outgoing") as span:
            with httpx.Client() as client:
                response = client.get(f"http://127.0.0.1:{server.server_address[1]}/")
        trace_id = f"{span.get_span_context().trace_id:032x}"
    finally:
        server.shutdown()
        thread.join()

    traceparent = received["traceparent"]
    assert response.status_code == 200
    assert traceparent is not None
    assert traceparent.split("-")[1] == trace_id


def test_no_otlp_exporter_when_endpoint_is_empty(monkeypatch) -> None:
    """With no endpoint configured, no exporter is created and no spans leave the process."""
    created: list[dict[str, object]] = []

    def fake_exporter(**kwargs: object) -> InMemorySpanExporter:
        created.append(kwargs)
        return InMemorySpanExporter()

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", fake_exporter)

    provider = telemetry.create_tracer_provider("tracing-no-export", endpoint="")
    provider.shutdown()

    assert created == []


def test_otlp_exporter_is_added_when_endpoint_is_set(monkeypatch) -> None:
    """A configured endpoint becomes one OTLP/HTTP exporter posting to /v1/traces."""
    created: list[dict[str, object]] = []

    def fake_exporter(**kwargs: object) -> InMemorySpanExporter:
        created.append(kwargs)
        return InMemorySpanExporter()

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", fake_exporter)

    provider = telemetry.create_tracer_provider("tracing-export", endpoint="http://alloy:4318")
    provider.shutdown()

    assert created == [{"endpoint": "http://alloy:4318/v1/traces"}]
