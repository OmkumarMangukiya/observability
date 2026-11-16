"""Tests for the dependency-timeout fault (M3.6)."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from structlog.testing import capture_logs

from common.faults import active_faults, clear_faults, load_from_env
from orders.main import app as orders_app
from orders.main import get_payments_client
from payments.main import app as payments_app

orders_client = TestClient(orders_app)

PROCESS_PATH = "/payments/process"
PAYLOAD = {"order_id": "order-timeout", "amount": 42.0}
# How long payments hangs when the fault is on.
HANG_SECONDS = 2.0
# How long orders waits before giving up.
ORDERS_TIMEOUT_SECONDS = 0.25


@pytest.fixture(autouse=True)
def _no_faults() -> Iterator[None]:
    """Every test starts and ends with no faults active."""
    clear_faults()
    yield
    clear_faults()


@pytest.fixture(scope="module")
def live_payments() -> Iterator[str]:
    """Serve the real payments app on a random localhost port."""
    server = uvicorn.Server(
        uvicorn.Config(payments_app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("payments server did not start")
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        # A request may still be sleeping in the fault handler; do not wait for it.
        server.force_exit = True
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Collect finished spans from the process-wide tracer provider."""
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter


@contextmanager
def orders_talking_to(base_url: str, timeout: float) -> Iterator[None]:
    """Point orders at a real payments URL with the given client timeout."""
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        orders_app.dependency_overrides[get_payments_client] = lambda: client
        try:
            yield
        finally:
            orders_app.dependency_overrides.clear()


def _hang_payments(base_url: str, seconds: float = HANG_SECONDS) -> None:
    """Activate the dependency-timeout fault on the live payments server."""
    response = httpx.post(
        f"{base_url}/faults",
        json={
            "fault": "dependency_timeout",
            "enabled": True,
            "params": {"seconds": seconds},
        },
    )
    assert response.status_code == 200


def test_env_variable_activates_the_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_DEPENDENCY_TIMEOUT=true turns the fault on with its defaults."""
    monkeypatch.setenv("FAULT_DEPENDENCY_TIMEOUT", "true")

    load_from_env()

    assert active_faults() == {"dependency_timeout": {}}


def test_fault_delays_only_the_process_endpoint() -> None:
    """The fault hangs POST /payments/process but leaves health checks alone."""
    client = TestClient(payments_app)
    toggled = client.post(
        "/faults",
        json={
            "fault": "dependency_timeout",
            "enabled": True,
            "params": {"seconds": 0.2},
        },
    )
    assert toggled.status_code == 200

    started = time.perf_counter()
    response = client.post(PROCESS_PATH, json=PAYLOAD)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed >= 0.2

    started = time.perf_counter()
    assert client.get("/healthz").status_code == 200
    assert time.perf_counter() - started < 0.2


def test_orders_returns_504_when_payments_hangs(live_payments: str) -> None:
    """Orders gives up on the hung call well before payments would answer."""
    _hang_payments(live_payments)

    with orders_talking_to(live_payments, ORDERS_TIMEOUT_SECONDS):
        started = time.perf_counter()
        response = orders_client.get("/orders")
        elapsed = time.perf_counter() - started

    assert response.status_code == 504
    assert ORDERS_TIMEOUT_SECONDS * 0.8 <= elapsed < 1.0


def test_timeout_is_logged_with_structured_fields(live_payments: str) -> None:
    """The failure is logged once as payments_call_failed with a timeout reason."""
    _hang_payments(live_payments)

    with orders_talking_to(live_payments, ORDERS_TIMEOUT_SECONDS):
        with capture_logs() as logs:
            response = orders_client.get("/orders")

    assert response.status_code == 504
    failures = [entry for entry in logs if entry["event"] == "payments_call_failed"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["dependency"] == "payments"
    assert failure["reason"] == "timeout"
    assert failure["order_id"] in {"order-1", "order-2"}
    assert failure["timeout_seconds"] == ORDERS_TIMEOUT_SECONDS
    assert failure["error"]


def test_trace_shows_the_timed_out_call(
    live_payments: str, span_exporter: InMemorySpanExporter
) -> None:
    """The outbound span is an error after waiting, and orders ends on a 504."""
    span_exporter.clear()
    _hang_payments(live_payments)

    with orders_talking_to(live_payments, ORDERS_TIMEOUT_SECONDS):
        response = orders_client.get("/orders")

    assert response.status_code == 504
    spans = span_exporter.get_finished_spans()

    outbound = [
        span
        for span in spans
        if span.kind == SpanKind.CLIENT
        and span.attributes.get("http.method") == "POST"
        and "/payments/process" in str(span.attributes.get("http.url", ""))
    ]
    assert len(outbound) == 1
    payment_span = outbound[0]
    assert payment_span.status.status_code == StatusCode.ERROR
    waited = (payment_span.end_time - payment_span.start_time) / 1e9
    assert waited >= ORDERS_TIMEOUT_SECONDS * 0.8

    timed_out = [
        span
        for span in spans
        if span.kind == SpanKind.SERVER and span.attributes.get("http.status_code") == 504
    ]
    assert len(timed_out) == 1
    assert timed_out[0].status.status_code == StatusCode.ERROR
