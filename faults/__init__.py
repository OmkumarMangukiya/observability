"""Drill scenario catalog shared by the runner and its tests (M4.1)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")

# Services a drill may target, with the URL the runner uses from the host and
# the environment variable that overrides it.
SERVICE_DEFAULT_URLS: dict[str, str] = {
    "gateway": "http://localhost:8000",
    "orders": "http://localhost:8001",
    "payments": "http://localhost:8002",
}
SERVICE_URL_ENV: dict[str, str] = {
    "gateway": "GATEWAY_URL",
    "orders": "ORDERS_URL",
    "payments": "PAYMENTS_URL",
}

_SCENARIO_KEYS = frozenset(
    {
        "id",
        "service",
        "fault",
        "params",
        "traffic",
        "expected_symptom",
        "expected_alert",
    }
)
_REQUIRED_SCENARIO_KEYS = _SCENARIO_KEYS - {"expected_alert"}
_TRAFFIC_KEYS = frozenset({"service", "method", "path", "requests", "interval_seconds"})
_METHODS = frozenset({"GET", "POST"})


class CatalogError(ValueError):
    """The scenario catalog is missing or malformed."""


@dataclass(frozen=True)
class Traffic:
    """Requests the runner sends while a fault is on."""

    service: str
    method: str
    path: str
    requests: int
    interval_seconds: float


@dataclass(frozen=True)
class Scenario:
    """One drill: a fault to enable and the traffic that exposes it."""

    id: str
    service: str
    fault: str
    params: dict[str, Any]
    traffic: Traffic
    expected_symptom: str
    expected_alert: str | None


def _text(data: dict[str, Any], key: str, where: str) -> str:
    """Read a non-empty string field."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}.{key} must be a non-empty string")
    return value


def _positive_int(data: dict[str, Any], key: str, where: str) -> int:
    """Read an integer >= 1, rejecting booleans and floats."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CatalogError(f"{where}.{key} must be an integer >= 1")
    return value


def _non_negative_number(data: dict[str, Any], key: str, where: str) -> float:
    """Read a number >= 0, rejecting booleans."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise CatalogError(f"{where}.{key} must be a number >= 0")
    return float(value)


def _parse_traffic(raw: Any, where: str) -> Traffic:
    """Validate and build one Traffic entry."""
    if not isinstance(raw, dict):
        raise CatalogError(f"{where} must be an object")

    missing = _TRAFFIC_KEYS - raw.keys()
    if missing:
        raise CatalogError(f"{where} is missing keys: {sorted(missing)}")
    unknown = raw.keys() - _TRAFFIC_KEYS
    if unknown:
        raise CatalogError(f"{where} has unknown keys: {sorted(unknown)}")

    service = _text(raw, "service", where)
    if service not in SERVICE_DEFAULT_URLS:
        raise CatalogError(
            f"{where}.service {service!r} is unknown; expected one of "
            f"{sorted(SERVICE_DEFAULT_URLS)}"
        )

    method = _text(raw, "method", where).upper()
    if method not in _METHODS:
        raise CatalogError(f"{where}.method must be one of {sorted(_METHODS)}")

    path = _text(raw, "path", where)
    if not path.startswith("/"):
        raise CatalogError(f"{where}.path must start with '/'")

    return Traffic(
        service=service,
        method=method,
        path=path,
        requests=_positive_int(raw, "requests", where),
        interval_seconds=_non_negative_number(raw, "interval_seconds", where),
    )


def _parse_scenario(raw: Any, index: int) -> Scenario:
    """Validate and build one Scenario entry."""
    where = f"scenarios[{index}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where} must be an object")

    missing = _REQUIRED_SCENARIO_KEYS - raw.keys()
    if missing:
        raise CatalogError(f"{where} is missing keys: {sorted(missing)}")
    unknown = raw.keys() - _SCENARIO_KEYS
    if unknown:
        raise CatalogError(f"{where} has unknown keys: {sorted(unknown)}")

    scenario_id = _text(raw, "id", where)
    service = _text(raw, "service", where)
    if service not in SERVICE_DEFAULT_URLS:
        raise CatalogError(
            f"{where}.service {service!r} is unknown; expected one of "
            f"{sorted(SERVICE_DEFAULT_URLS)}"
        )

    fault = _text(raw, "fault", where)

    params = raw.get("params")
    if not isinstance(params, dict):
        raise CatalogError(f"{where}.params must be an object")

    expected_symptom = _text(raw, "expected_symptom", where)

    expected_alert = raw.get("expected_alert")
    if expected_alert is not None and (
        not isinstance(expected_alert, str) or not expected_alert.strip()
    ):
        raise CatalogError(f"{where}.expected_alert must be a non-empty string or null")

    return Scenario(
        id=scenario_id,
        service=service,
        fault=fault,
        params=dict(params),
        traffic=_parse_traffic(raw.get("traffic"), f"{where}.traffic"),
        expected_symptom=expected_symptom,
        expected_alert=expected_alert,
    )


def load_catalog(path: Path | None = None) -> list[Scenario]:
    """Load and validate the drill catalog.

    Raises:
        CatalogError: The file is missing, not valid JSON, or an entry is malformed.
    """
    catalog_path = CATALOG_PATH if path is None else path
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"cannot read catalog {catalog_path}: {exc}") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog {catalog_path} is not valid JSON: {exc}") from exc

    if not isinstance(document, dict) or set(document) != {"scenarios"}:
        raise CatalogError(f"catalog {catalog_path} must be an object with one key: scenarios")

    raw_scenarios = document["scenarios"]
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise CatalogError("scenarios must be a non-empty list")

    scenarios = [_parse_scenario(raw, index) for index, raw in enumerate(raw_scenarios)]

    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.id in seen:
            raise CatalogError(f"duplicate scenario id: {scenario.id}")
        seen.add(scenario.id)

    return scenarios


def get_scenario(scenario_id: str, scenarios: list[Scenario] | None = None) -> Scenario:
    """Return one scenario by id.

    Raises:
        CatalogError: No scenario has that id.
    """
    catalog = load_catalog() if scenarios is None else scenarios
    for scenario in catalog:
        if scenario.id == scenario_id:
            return scenario
    available = ", ".join(scenario.id for scenario in catalog)
    raise CatalogError(f"unknown scenario {scenario_id!r}; available: {available}")
