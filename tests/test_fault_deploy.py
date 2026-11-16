"""Tests for the bad-deploy fault (M3.7)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from common.faults import active_faults, clear_faults, load_from_env
from payments.main import app

client = TestClient(app)

PAYLOAD = {"order_id": "order-deploy", "amount": 12.5}
PROCESS_PATH = "/payments/process"


@pytest.fixture(autouse=True)
def _no_faults() -> Iterator[None]:
    """Every test starts and ends with no faults active."""
    clear_faults()
    yield
    clear_faults()


def _toggle(enabled: bool = True) -> None:
    """Turn the bad-deploy fault on or off through the HTTP control endpoint."""
    response = client.post(
        "/faults",
        json={"fault": "bad_deploy", "enabled": enabled},
    )
    assert response.status_code == 200


def test_env_variable_activates_the_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_BAD_DEPLOY=true turns the fault on with its defaults."""
    monkeypatch.setenv("FAULT_BAD_DEPLOY", "true")

    load_from_env()

    assert active_faults() == {"bad_deploy": {}}


def test_every_process_request_fails() -> None:
    """With the fault on every process call is a 500 carrying the fault marker."""
    _toggle()

    for _ in range(3):
        response = client.post(PROCESS_PATH, json=PAYLOAD)

        assert response.status_code == 500
        assert response.json()["fault"] == "bad_deploy"


def test_read_requests_fail_too() -> None:
    """The deploy is broken for reads as well, not only payment processing."""
    _toggle()

    response = client.get("/payments/payment-1")

    assert response.status_code == 500
    assert response.json()["fault"] == "bad_deploy"


def test_healthz_and_metrics_stay_up() -> None:
    """Infra probes keep working so the failure stays observable."""
    _toggle()

    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_control_endpoint_can_turn_the_fault_off() -> None:
    """A broken service must still be recoverable through POST /faults."""
    _toggle()

    _toggle(False)

    assert client.post(PROCESS_PATH, json=PAYLOAD).status_code == 200


def test_failures_are_logged_with_the_marker() -> None:
    """Every injected 500 writes one structured fault_injected log line."""
    _toggle()

    with capture_logs() as logs:
        response = client.post(PROCESS_PATH, json=PAYLOAD)

    assert response.status_code == 500
    injected = [entry for entry in logs if entry["event"] == "fault_injected"]
    assert len(injected) == 1
    assert injected[0]["fault"] == "bad_deploy"
    assert injected[0]["path"] == PROCESS_PATH


def test_deploy_marker_is_logged_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup emits one deploy marker the agent can correlate with the errors."""
    monkeypatch.setenv("FAULT_BAD_DEPLOY", "true")
    load_from_env()

    with capture_logs() as logs:
        with TestClient(app):
            pass

    markers = [entry for entry in logs if entry["event"] == "deploy"]
    assert len(markers) == 1
    assert markers[0]["service"] == "payments"
    assert markers[0]["git_sha"]
    assert markers[0]["fault"] == "bad_deploy"


def test_no_deploy_marker_without_the_fault() -> None:
    """A normal startup does not pretend that a bad deploy happened."""
    with capture_logs() as logs:
        with TestClient(app):
            pass

    assert [entry for entry in logs if entry["event"] == "deploy"] == []
