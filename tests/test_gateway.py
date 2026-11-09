"""Tests for the gateway service (M1.6)."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi.testclient import TestClient

from gateway.main import app, get_orders_client

client = TestClient(app)

Handler = Callable[[httpx.Request], httpx.Response]


@contextmanager
def orders_action(handler: Handler) -> Iterator[list[httpx.Request]]:
    """Point the gateway at a fake orders service and collect what it receives."""
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    with httpx.Client(
        transport=httpx.MockTransport(record), base_url="http://orders:8001"
    ) as fake_orders:
        app.dependency_overrides[get_orders_client] = lambda: fake_orders
        try:
            yield sent
        finally:
            app.dependency_overrides.clear()


def test_list_orders_proxies_orders_response() -> None:
    """GET /orders returns the orders service's own body and status."""
    with orders_action(lambda request: httpx.Response(200, json=[{"id": "order-1"}])) as sent:
        response = client.get("/orders")

    assert response.status_code == 200
    assert response.json() == [{"id": "order-1"}]
    assert str(sent[0].url) == "http://orders:8001/orders"


def test_unknown_order_404_is_passed_through() -> None:
    """A 404 from orders stays a 404 with its body intact."""
    with orders_action(lambda request: httpx.Response(404, json={"detail": "order not found"})):
        response = client.get("/orders/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "order not found"}


def test_orders_connect_error_becomes_502() -> None:
    """When orders cannot be reached, the gateway answers 502."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("orders is down", request=request)

    with orders_action(handler):
        response = client.get("/orders")

    assert response.status_code == 502


def test_traceparent_is_forwarded_to_orders() -> None:
    """The outgoing call carries the gateway request's trace context."""
    received: dict[str, str | None] = {}

    class Recorder(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received["traceparent"] = self.headers.get("traceparent")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, format: str, *args: object) -> None:
            """Silence the default request log."""

    # The OTel httpx instrumentation wraps HTTPTransport, so this needs a real
    # transport; httpx.MockTransport would bypass it.
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(base_url=base_url) as fake_orders:
            app.dependency_overrides[get_orders_client] = lambda: fake_orders
            try:
                response = client.get("/orders")
            finally:
                app.dependency_overrides.clear()
    finally:
        server.shutdown()
        thread.join()

    traceparent = received["traceparent"]
    assert response.status_code == 200
    assert traceparent is not None
    trace_id = traceparent.split("-")[1]
    assert len(trace_id) == 32
    assert all(char in "0123456789abcdef" for char in trace_id)


def test_healthz_returns_200() -> None:
    """The health probe does not depend on the orders service."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
