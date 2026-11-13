"""Tests for the slow-endpoint fault (M3.4)."""

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from common.faults import active_faults, clear_faults, load_from_env
from payments.main import app

client = TestClient(app)

PAYLOAD = {"order_id": "order-slow", "amount": 12.5}
PROCESS_PATH = "/payments/process"


@pytest.fixture(autouse=True)
def _no_faults() -> Iterator[None]:
    """Every test starts and ends with no faults active."""
    clear_faults()
    yield
    clear_faults()


def _bucket_count(le: str) -> float:
    """Read one latency-histogram bucket for POST /payments/process."""
    text = client.get("/metrics").text
    total = 0.0
    for line in text.splitlines():
        if (
            line.startswith("http_server_request_duration_seconds_bucket{")
            and 'http_route="/payments/process"' in line
            and f'le="{le}",' in line
        ):
            total += float(line.rsplit(" ", 1)[1])
    return total


def test_inactive_fault_keeps_processing_fast() -> None:
    """With no fault active, a payment is processed in under 500 ms."""
    started = time.perf_counter()
    response = client.post(PROCESS_PATH, json=PAYLOAD)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 0.5


def test_active_fault_delays_the_processing_endpoint() -> None:
    """An HTTP toggle with seconds=0.3 adds at least that much latency."""
    toggled = client.post(
        "/faults",
        json={"fault": "slow_endpoint", "enabled": True, "params": {"seconds": 0.3}},
    )
    assert toggled.status_code == 200

    started = time.perf_counter()
    response = client.post(PROCESS_PATH, json=PAYLOAD)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed >= 0.3


def test_env_variable_activates_the_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_SLOW_ENDPOINT=0.25 turns the fault on with that many seconds."""
    monkeypatch.setenv("FAULT_SLOW_ENDPOINT", "0.25")

    load_from_env()

    assert active_faults() == {"slow_endpoint": {"seconds": 0.25}}


def test_latency_histogram_records_the_inflated_duration() -> None:
    """A one-second delay lands in the 2.5 s bucket, not the 0.5 s bucket."""
    before_short = _bucket_count("0.5")
    before_long = _bucket_count("2.5")

    client.post(
        "/faults",
        json={"fault": "slow_endpoint", "enabled": True, "params": {"seconds": 1.0}},
    )
    response = client.post(PROCESS_PATH, json=PAYLOAD)

    assert response.status_code == 200
    assert _bucket_count("0.5") - before_short == 0
    assert _bucket_count("2.5") - before_long == 1
