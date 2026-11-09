"""Tests for the orders service (M1.5)."""

from fastapi.testclient import TestClient

from orders.main import app

client = TestClient(app)


def test_list_orders_returns_200() -> None:
    """The list endpoint returns 200 and a non-empty list."""
    response = client.get("/orders")

    assert response.status_code == 200
    assert response.json()


def test_known_order_returns_200() -> None:
    """A known id returns that order."""
    response = client.get("/orders/order-1")

    assert response.status_code == 200
    assert response.json()["id"] == "order-1"


def test_unknown_order_returns_404() -> None:
    """An unknown id is a 404."""
    response = client.get("/orders/does-not-exist")

    assert response.status_code == 404


def test_healthz_returns_200() -> None:
    """The health probe answers without touching the orders."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_carry_orders_service_name() -> None:
    """The orders app is wired to observability with its own service name."""
    client.get("/orders")
    text = client.get("/metrics").text

    assert 'service_name="orders"' in text
    assert 'http_route="/orders"' in text
