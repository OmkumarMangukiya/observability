"""Tests for the random-500s fault (M3.5)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from common.faults import active_faults, clear_faults, load_from_env
from payments.main import app

client = TestClient(app)

PAYLOAD = {"order_id": "order-500s", "amount": 9.99}
PROCESS_PATH = "/payments/process"


@pytest.fixture(autouse=True)
def _no_faults() -> Iterator[None]:
    """Every test starts and ends with no faults active."""
    clear_faults()
    yield
    clear_faults()


def _toggle(rate: float) -> None:
    """Activate the fault at the given rate through the HTTP control endpoint."""
    response = client.post(
        "/faults",
        json={"fault": "random_500s", "enabled": True, "params": {"rate": rate}},
    )
    assert response.status_code == 200


def _injected_500s() -> float:
    """Sum the POST 500 counter series served at /metrics."""
    text = client.get("/metrics").text
    total = 0.0
    for line in text.splitlines():
        if (
            line.startswith("http_server_requests_total{")
            and 'http_request_method="POST"' in line
            and 'http_response_status_code="500"' in line
        ):
            total += float(line.rsplit(" ", 1)[1])
    return total


def test_rate_one_fails_every_request() -> None:
    """With rate=1.0 every process call is a 500 carrying the fault marker."""
    _toggle(1.0)

    for _ in range(3):
        response = client.post(PROCESS_PATH, json=PAYLOAD)

        assert response.status_code == 500
        assert response.json()["fault"] == "random_500s"


def test_rate_zero_never_fails() -> None:
    """With rate=0.0 no process call fails."""
    _toggle(0.0)

    for _ in range(10):
        assert client.post(PROCESS_PATH, json=PAYLOAD).status_code == 200


def test_error_counter_counts_injected_500s() -> None:
    """The 500 counter rises by exactly the number of injected failures."""
    _toggle(1.0)
    before = _injected_500s()

    for _ in range(3):
        assert client.post(PROCESS_PATH, json=PAYLOAD).status_code == 500

    assert _injected_500s() - before == 3


def test_injected_failure_is_logged_with_the_marker() -> None:
    """Every injected 500 writes one structured fault_injected log line."""
    _toggle(1.0)

    with capture_logs() as logs:
        response = client.post(PROCESS_PATH, json=PAYLOAD)

    assert response.status_code == 500
    injected = [entry for entry in logs if entry["event"] == "fault_injected"]
    assert len(injected) == 1
    assert injected[0]["fault"] == "random_500s"
    assert injected[0]["path"] == PROCESS_PATH


def test_env_variable_activates_the_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_RANDOM_500S=0.3 turns the fault on with that rate."""
    monkeypatch.setenv("FAULT_RANDOM_500S", "0.3")

    load_from_env()

    assert active_faults() == {"random_500s": {"rate": 0.3}}


def test_healthz_is_not_affected() -> None:
    """A fault aimed at the process endpoint leaves health checks alone."""
    _toggle(1.0)

    response = client.get("/healthz")

    assert response.status_code == 200
