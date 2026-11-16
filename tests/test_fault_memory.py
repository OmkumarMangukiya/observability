"""Tests for the memory-leak fault (M3.7)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from common.faults import active_faults, clear_faults, load_from_env
from payments.main import app

client = TestClient(app)

PAYLOAD = {"order_id": "order-leak", "amount": 3.5}
PROCESS_PATH = "/payments/process"
BYTES_PER_REQUEST = 1_000_000
REQUESTS = 24


@pytest.fixture(autouse=True)
def _no_faults() -> Iterator[None]:
    """Every test starts and ends with no faults active."""
    clear_faults()
    yield
    clear_faults()


def _toggle(**params: int) -> None:
    """Activate the leak through the HTTP control endpoint."""
    response = client.post(
        "/faults",
        json={"fault": "memory_leak", "enabled": True, "params": params},
    )
    assert response.status_code == 200


def _resident_memory() -> float:
    """Read process_resident_memory_bytes from the Prometheus endpoint."""
    text = client.get("/metrics").text
    values = [
        float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line.startswith("process_resident_memory_bytes")
    ]
    assert len(values) == 1
    return values[0]


def test_process_memory_metric_is_exported() -> None:
    """The standard process metrics are scraped alongside the HTTP metrics."""
    assert _resident_memory() > 0


def test_env_variable_activates_the_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_MEMORY_LEAK=true turns the leak on with its defaults."""
    monkeypatch.setenv("FAULT_MEMORY_LEAK", "true")

    load_from_env()

    assert active_faults() == {"memory_leak": {}}


def test_requests_keep_succeeding_while_leaking() -> None:
    """The leak does not break responses; it only consumes memory."""
    _toggle(bytes_per_request=1024)

    assert client.post(PROCESS_PATH, json=PAYLOAD).status_code == 200


def test_resident_memory_grows_after_requests() -> None:
    """RSS climbs by roughly the leaked amount over N requests."""
    _toggle(bytes_per_request=BYTES_PER_REQUEST)
    before = _resident_memory()

    for _ in range(REQUESTS):
        assert client.post(PROCESS_PATH, json=PAYLOAD).status_code == 200

    after = _resident_memory()

    assert after - before >= REQUESTS * BYTES_PER_REQUEST * 0.5


def test_healthz_does_not_leak() -> None:
    """Infra probes are skipped, same as the other fault scenarios."""
    _toggle(bytes_per_request=BYTES_PER_REQUEST)
    before = _resident_memory()

    for _ in range(REQUESTS):
        assert client.get("/healthz").status_code == 200

    after = _resident_memory()

    assert after - before < REQUESTS * BYTES_PER_REQUEST * 0.5
