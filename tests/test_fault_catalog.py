"""Tests for the drill catalog and runner (M4.1)."""

import importlib
import json
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from common.faults import clear_faults
from faults import CatalogError, Scenario, get_scenario, load_catalog
from faults.run import disable, enable

SERVICE_MODULES = {
    "gateway": "gateway.main",
    "orders": "orders.main",
    "payments": "payments.main",
}
EXPECTED_SCENARIOS = {
    "random_500s",
    "bad_deploy",
    "slow_endpoint",
    "dependency_timeout",
    "memory_leak",
}


@pytest.fixture(autouse=True)
def _no_active_faults() -> Iterator[None]:
    """Keep scenarios from leaking between tests."""
    clear_faults()
    yield
    clear_faults()


@pytest.fixture(scope="module")
def payments_server() -> Iterator[str]:
    """Run the real payments app on a random port so the runner talks over HTTP."""
    from payments.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="payments-test-server", daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("payments test server did not start")
        time.sleep(0.02)

    socket = server.servers[0].sockets[0]
    host, port = socket.getsockname()[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _catalog_document(scenario: dict[str, Any]) -> dict[str, Any]:
    """Wrap one scenario in a catalog document."""
    return {"scenarios": [scenario]}


def _scenario_entry() -> dict[str, Any]:
    """Return a valid catalog entry that tests may corrupt."""
    return {
        "id": "random_500s",
        "service": "payments",
        "fault": "random_500s",
        "params": {"rate": 0.3},
        "traffic": {
            "service": "gateway",
            "method": "GET",
            "path": "/orders",
            "requests": 1,
            "interval_seconds": 0,
        },
        "expected_symptom": "Some requests fail.",
    }


def _write_catalog(path: Path, document: dict[str, Any]) -> Path:
    """Write one catalog document and return its path."""
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _get_faults(base_url: str) -> list[dict[str, Any]]:
    """GET /faults from the live test service."""
    with urllib.request.urlopen(f"{base_url}/faults", timeout=5) as response:
        return json.loads(response.read())


def test_catalog_lists_every_phase_4_drill() -> None:
    """The catalog loads and covers all five HTTP-toggle drills."""
    scenarios = load_catalog()
    ids = [scenario.id for scenario in scenarios]

    assert len(ids) == len(set(ids))
    assert EXPECTED_SCENARIOS <= set(ids)
    for scenario in scenarios:
        assert isinstance(scenario.params, dict)
        assert scenario.traffic.requests >= 1
        assert scenario.traffic.path.startswith("/")
        assert scenario.expected_symptom.strip()


def test_catalog_rejects_an_unknown_service(tmp_path: Path) -> None:
    """A scenario targeting a service the runner cannot reach fails to load."""
    entry = _scenario_entry()
    entry["service"] = "cdn"

    with pytest.raises(CatalogError, match="service"):
        load_catalog(_write_catalog(tmp_path / "catalog.json", _catalog_document(entry)))


def test_catalog_rejects_duplicate_scenario_ids(tmp_path: Path) -> None:
    """Two drills with the same id would make `run <id>` ambiguous."""
    entry = _scenario_entry()

    document = {"scenarios": [entry, dict(entry)]}
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(_write_catalog(tmp_path / "catalog.json", document))


def test_every_scenario_names_a_fault_its_service_registers() -> None:
    """Each catalog entry is accepted by its target service's /faults endpoint."""
    for scenario in load_catalog():
        service_app = importlib.import_module(SERVICE_MODULES[scenario.service]).app
        client = TestClient(service_app)

        response = client.post(
            "/faults",
            json={
                "fault": scenario.fault,
                "enabled": True,
                "params": scenario.params,
            },
        )

        assert response.status_code == 200, f"{scenario.id}: {response.text}"
        active = {fault["fault"]: fault["params"] for fault in response.json()}
        assert active == {scenario.fault: scenario.params}

        client.post("/faults", json={"fault": scenario.fault, "enabled": False})


def test_enable_and_disable_reach_faults_and_are_idempotent(payments_server: str) -> None:
    """The runner's HTTP calls toggle the live service, and repeats are harmless."""
    scenario: Scenario = get_scenario("random_500s")

    first = enable(scenario, base_url=payments_server)
    second = enable(scenario, base_url=payments_server)

    expected = [{"fault": "random_500s", "enabled": True, "params": {"rate": 0.3}}]
    assert first == second == expected
    assert _get_faults(payments_server) == expected

    assert disable(scenario, base_url=payments_server) == []
    assert disable(scenario, base_url=payments_server) == []
    assert _get_faults(payments_server) == []
