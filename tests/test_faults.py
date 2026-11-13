"""Tests for the fault-injection framework (M3.3)."""

import importlib
import os
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse, Response

from common.faults import (
    FaultRequest,
    active_faults,
    clear_faults,
    init_faults,
    load_from_env,
    register_fault,
)

CALLS: list[FaultRequest] = []


@register_fault("test_marker")
def _marker(request: FaultRequest, params: Mapping[str, Any]) -> Response | None:
    CALLS.append(request)
    return None


@register_fault("test_error")
def _error(request: FaultRequest, params: Mapping[str, Any]) -> Response | None:
    return JSONResponse(status_code=500, content={"detail": "injected"})


@pytest.fixture(autouse=True)
def _isolated_faults(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start and end every test with no faults and no FAULT_* variables."""
    for key in [name for name in os.environ if name.startswith("FAULT_")]:
        monkeypatch.delenv(key, raising=False)
    clear_faults()
    CALLS.clear()
    yield
    clear_faults()
    CALLS.clear()


def _build_app() -> FastAPI:
    """Return a minimal app with the framework mounted."""
    application = FastAPI()
    init_faults(application)

    @application.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    return application


@pytest.fixture
def app(_isolated_faults: None) -> FastAPI:
    """A fresh app, mounted before each test."""
    return _build_app()


def test_no_faults_are_active_after_mount(app: FastAPI) -> None:
    """A freshly mounted service has nothing active."""
    response = TestClient(app).get("/faults")

    assert response.status_code == 200
    assert response.json() == []


def test_unknown_fault_returns_400(app: FastAPI) -> None:
    """Toggling a name that was never registered is a client error."""
    response = TestClient(app).post("/faults", json={"fault": "not_a_fault", "enabled": True})

    assert response.status_code == 400


def test_fault_can_be_toggled_on_and_off_via_http(app: FastAPI) -> None:
    """An HTTP toggle activates the handler and a second toggle turns it off."""
    client = TestClient(app)

    enabled = client.post("/faults", json={"fault": "test_marker", "enabled": True})
    assert enabled.status_code == 200
    assert enabled.json() == [{"fault": "test_marker", "enabled": True, "params": {}}]

    assert client.get("/ping").status_code == 200
    assert [request.path for request in CALLS] == ["/ping"]

    disabled = client.post("/faults", json={"fault": "test_marker", "enabled": False})
    assert disabled.status_code == 200
    assert disabled.json() == []

    CALLS.clear()
    client.get("/ping")
    assert CALLS == []


def test_active_fault_can_inject_a_response(app: FastAPI) -> None:
    """A handler that returns a Response short-circuits the route."""
    client = TestClient(app)
    client.post("/faults", json={"fault": "test_error", "enabled": True})

    response = client.get("/ping")

    assert response.status_code == 500
    assert response.json() == {"detail": "injected"}


def test_fault_control_endpoints_ignore_faults(app: FastAPI) -> None:
    """A fault that breaks everything must not lock out the control endpoint."""
    client = TestClient(app)
    client.post("/faults", json={"fault": "test_error", "enabled": True})

    listing = client.get("/faults")
    disabled = client.post("/faults", json={"fault": "test_error", "enabled": False})

    assert listing.status_code == 200
    assert [fault["fault"] for fault in listing.json()] == ["test_error"]
    assert disabled.status_code == 200
    assert client.get("/ping").status_code == 200


def test_env_true_activates_fault_on_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_<NAME>=true is enough to enable a fault at startup."""
    monkeypatch.setenv("FAULT_TEST_MARKER", "true")

    response = TestClient(_build_app()).get("/faults")

    assert response.json() == [{"fault": "test_marker", "enabled": True, "params": {}}]


def test_env_scalar_becomes_the_faults_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAULT_<NAME>=0.3 enables it with a numeric value under the named param."""
    monkeypatch.setenv("FAULT_TEST_MARKER", "0.3")

    load_from_env()

    assert active_faults() == {"test_marker": {"value": 0.3}}


def test_env_false_turns_the_fault_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the variable to false deactivates the fault again."""
    monkeypatch.setenv("FAULT_TEST_MARKER", "true")
    load_from_env()

    monkeypatch.setenv("FAULT_TEST_MARKER", "false")
    load_from_env()

    assert active_faults() == {}


def test_unknown_env_fault_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a FAULT_* variable must not stop the service from starting."""
    monkeypatch.setenv("FAULT_NOT_REGISTERED", "true")

    load_from_env()

    assert active_faults() == {}


@pytest.mark.parametrize("module", ["gateway.main", "orders.main", "payments.main"])
def test_each_service_mounts_the_framework(module: str) -> None:
    """Every service answers GET /faults with an empty list when nothing is on."""
    service_app = importlib.import_module(module).app

    response = TestClient(service_app).get("/faults")

    assert response.status_code == 200
    assert response.json() == []
