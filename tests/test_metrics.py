"""Tests for metrics and request middleware (M1.3)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.telemetry import init_observability


def _make_app(service: str) -> FastAPI:
    """Build a tiny app with the observability wiring and three routes."""
    app = FastAPI()
    init_observability(app, service)

    @app.get("/orders")
    def list_orders() -> list[dict[str, str]]:
        return [{"id": "order-1"}]

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _scrape(client: TestClient) -> str:
    """Return the Prometheus text served at /metrics."""
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


def test_counter_and_histogram_have_contract_labels() -> None:
    """Both instruments export the four agreed labels."""
    client = TestClient(_make_app("orders"))
    client.get("/orders")

    text = _scrape(client)

    assert "http_server_requests_total" in text
    assert "http_server_request_duration_seconds_bucket" in text
    for label in (
        'service_name="orders"',
        'http_request_method="GET"',
        'http_route="/orders"',
        'http_response_status_code="200"',
    ):
        assert label in text


def test_metrics_endpoint_returns_prometheus_text() -> None:
    """GET /metrics serves the Prometheus text exposition format."""
    client = TestClient(_make_app("orders-text"))
    client.get("/orders")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "# TYPE http_server_requests_total counter" in response.text
    assert "# TYPE http_server_request_duration_seconds histogram" in response.text


def test_metrics_and_healthz_are_not_recorded() -> None:
    """The metrics and health endpoints must not pollute the series."""
    client = TestClient(_make_app("orders-skip"))

    client.get("/healthz")
    client.get("/metrics")

    text = _scrape(client)

    assert "http_server_requests_total" not in text
    assert "http_server_request_duration_seconds" not in text


def test_second_init_does_not_duplicate_series() -> None:
    """Initialising the same app twice still records one request once."""
    app = FastAPI()
    init_observability(app, "gateway-init")
    init_observability(app, "gateway-init")

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    client.get("/ping")

    text = _scrape(client)
    series = [line for line in text.splitlines() if line.startswith("http_server_requests_total{")]

    assert len(series) == 1
    assert series[0].endswith(" 1.0")


def test_unhandled_exception_is_recorded_as_500() -> None:
    """A raised exception is recorded as a 500 and re-raised to the caller."""
    client = TestClient(_make_app("orders-errors"))

    with pytest.raises(RuntimeError, match="boom"):
        client.get("/boom")

    text = _scrape(client)

    assert 'http_route="/boom"' in text
    assert 'http_response_status_code="500"' in text
