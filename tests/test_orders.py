"""Tests for the orders service (M1.5, M3.2)."""

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi.testclient import TestClient

from orders.main import app, get_payments_client

client = TestClient(app)

Handler = Callable[[httpx.Request], httpx.Response]


def confirmed_payment(request: httpx.Request) -> httpx.Response:
    """Answer like payments: confirm the order with its id and amount."""
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "id": "payment-1",
            "order_id": body["order_id"],
            "amount": body["amount"],
            "status": "confirmed",
        },
    )


@contextmanager
def payments_action(handler: Handler = confirmed_payment) -> Iterator[list[httpx.Request]]:
    """Point orders at a fake payments service and collect what it receives."""
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    with httpx.Client(
        transport=httpx.MockTransport(record), base_url="http://payments:8002"
    ) as fake_payments:
        app.dependency_overrides[get_payments_client] = lambda: fake_payments
        try:
            yield sent
        finally:
            app.dependency_overrides.clear()


def test_list_orders_returns_200() -> None:
    """The list endpoint returns 200 and a non-empty list."""
    with payments_action():
        response = client.get("/orders")

    assert response.status_code == 200
    assert response.json()


def test_list_orders_attaches_a_payment_per_order() -> None:
    """Each listed order triggers one payment call and carries the result."""
    with payments_action() as sent:
        response = client.get("/orders")

    orders = response.json()
    assert all(order["payment"]["status"] == "confirmed" for order in orders)

    assert len(sent) == len(orders)
    assert all(request.method == "POST" for request in sent)
    assert all(request.url.path == "/payments/process" for request in sent)
    assert [json.loads(request.content)["order_id"] for request in sent] == [
        order["id"] for order in orders
    ]


def test_payments_unavailable_returns_502() -> None:
    """When payments cannot be reached, listing orders answers 502."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("payments is down", request=request)

    with payments_action(handler):
        response = client.get("/orders")

    assert response.status_code == 502


def test_known_order_returns_200() -> None:
    """A known id returns that order without processing a payment."""
    response = client.get("/orders/order-1")

    assert response.status_code == 200
    assert response.json()["id"] == "order-1"


def test_unknown_order_returns_404() -> None:
    """An unknown id is a 404."""
    response = client.get("/orders/does-not-exist")

    assert response.status_code == 404


def test_healthz_returns_200() -> None:
    """The health probe answers without touching orders or payments."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_traceparent_is_forwarded_to_payments() -> None:
    """The outgoing payment call carries the orders request's trace context."""
    received: dict[str, str | None] = {}

    class Recorder(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            received["traceparent"] = self.headers.get("traceparent")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id": "payment-1", "status": "confirmed"}')

        def log_message(self, format: str, *args: object) -> None:
            """Silence the default request log."""

    # The OTel httpx instrumentation wraps HTTPTransport, so this needs a real
    # transport; httpx.MockTransport would bypass it.
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(base_url=base_url) as fake_payments:
            app.dependency_overrides[get_payments_client] = lambda: fake_payments
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


def test_metrics_carry_orders_service_name() -> None:
    """The orders app is wired to observability with its own service name."""
    with payments_action():
        client.get("/orders")
    text = client.get("/metrics").text

    assert 'service_name="orders"' in text
    assert 'http_route="/orders"' in text
