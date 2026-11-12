"""Tests for the payments service (M3.1)."""

from fastapi.testclient import TestClient

from payments.main import app

client = TestClient(app)


def test_process_payment_returns_200() -> None:
    """Processing a payment returns a confirmation with a generated id."""
    response = client.post("/payments/process", json={"order_id": "order-1", "amount": 42.5})

    assert response.status_code == 200
    body = response.json()
    assert body["order_id"] == "order-1"
    assert body["amount"] == 42.5
    assert body["status"] == "confirmed"
    assert body["id"].startswith("payment-")


def test_processed_payment_can_be_fetched() -> None:
    """A payment returned by POST is stored and can be read back."""
    created = client.post("/payments/process", json={"order_id": "order-2", "amount": 10.0}).json()

    response = client.get(f"/payments/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_unknown_payment_returns_404() -> None:
    """An unknown id is a 404."""
    response = client.get("/payments/does-not-exist")

    assert response.status_code == 404


def test_healthz_returns_200() -> None:
    """The health probe answers without touching the payments store."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_carry_payments_service_name() -> None:
    """The payments app is wired to observability with its own service name."""
    client.post("/payments/process", json={"order_id": "order-3", "amount": 5.0})
    text = client.get("/metrics").text

    assert 'service_name="payments"' in text
    assert 'http_route="/payments/process"' in text
