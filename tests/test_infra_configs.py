"""Tests for infra configuration files (M1.8)."""

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
PROMETHEUS_CONFIG_PATH = REPO_ROOT / "infra" / "prometheus" / "prometheus.yml"
DATASOURCES_PATH = (
    REPO_ROOT / "infra" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
)
GRAFANA_DIR = REPO_ROOT / "infra" / "grafana"

# System paths (Docker socket) have no relative form; everything else must be
# relative so the repo works from any checkout location.
ALLOWED_ABSOLUTE_PREFIXES = ("/var/run/",)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML file, asserting it is a non-empty mapping."""
    assert path.is_file(), f"missing config file: {path.relative_to(REPO_ROOT)}"
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict), f"{path.name} is not a YAML mapping"
    return data


def _load_compose() -> dict[str, Any]:
    """Return the parsed docker-compose.yml."""
    return _load_yaml(COMPOSE_PATH)


def _datasources() -> list[dict[str, Any]]:
    """Return the Grafana datasources declared for provisioning."""
    datasources = _load_yaml(DATASOURCES_PATH).get("datasources", [])
    assert isinstance(datasources, list) and datasources, "no datasources provisioned"
    return datasources


def _container_ports(service: dict[str, Any]) -> set[str]:
    """Container-side ports from "host:container" publish entries."""
    ports: set[str] = set()
    for entry in service.get("ports", []):
        text = str(entry)
        if ":" in text:
            ports.add(text.rsplit(":", 1)[1].split("/", 1)[0])
    return ports


def _dashboard_datasource_uids(node: Any) -> set[str]:
    """Collect datasource UID references from a dashboard JSON tree."""
    uids: set[str] = set()
    if isinstance(node, dict):
        datasource = node.get("datasource")
        if isinstance(datasource, dict) and isinstance(datasource.get("uid"), str):
            uids.add(datasource["uid"])
        for child in node.values():
            uids |= _dashboard_datasource_uids(child)
    elif isinstance(node, list):
        for child in node:
            uids |= _dashboard_datasource_uids(child)
    return uids


def test_infra_yaml_files_parse() -> None:
    """Each config file exists and parses as YAML."""
    for path in (COMPOSE_PATH, PROMETHEUS_CONFIG_PATH, DATASOURCES_PATH):
        assert _load_yaml(path)


def test_compose_images_have_pinned_tags() -> None:
    """Every image uses an explicit tag, never latest or a bare name."""
    for name, service in _load_compose()["services"].items():
        image = service.get("image")
        assert image, f"service {name} has no image"
        last_segment = image.rsplit("/", 1)[-1]
        assert ":" in last_segment, f"service {name} image is not tagged: {image}"
        tag = last_segment.split(":", 1)[1]
        assert tag and tag != "latest", f"service {name} image is not pinned: {image}"


def test_compose_bind_mounts_are_relative() -> None:
    """Host paths in volumes are relative to the repo, never machine-specific."""
    for name, service in _load_compose()["services"].items():
        for volume in service.get("volumes", []):
            if not isinstance(volume, str) or ":" not in volume:
                continue
            source = volume.split(":", 1)[0]
            if source.startswith(ALLOWED_ABSOLUTE_PREFIXES):
                continue
            assert not source.startswith("/"), (
                f"service {name} mounts an absolute host path: {source}"
            )


def test_named_volumes_are_declared() -> None:
    """Every non-path volume source is declared in the top-level volumes block."""
    compose = _load_compose()
    declared = set(compose.get("volumes", {}))
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if not isinstance(volume, str) or ":" not in volume:
                continue
            source = volume.split(":", 1)[0]
            if source.startswith((".", "/")):
                continue
            assert source in declared, f"service {name} uses undeclared volume {source}"


def test_prometheus_targets_match_compose_services() -> None:
    """Every scrape target names a compose service and one of its ports."""
    services = _load_compose()["services"]
    jobs = _load_yaml(PROMETHEUS_CONFIG_PATH).get("scrape_configs", [])
    targets = [
        target
        for job in jobs
        for config in job.get("static_configs", [])
        for target in config.get("targets", [])
    ]

    assert targets, "prometheus.yml defines no scrape targets"
    for target in targets:
        host, _, port = target.rpartition(":")
        assert host in services, f"target {target} names no compose service"
        assert port in _container_ports(services[host]), (
            f"target {target} does not match a published container port"
        )


def test_grafana_prometheus_datasource_is_provisioned() -> None:
    """Prometheus is provisioned with the fixed UID, proxied, and default."""
    by_uid = {datasource.get("uid"): datasource for datasource in _datasources()}
    assert "prometheus" in by_uid, "datasource UID 'prometheus' is missing"
    datasource = by_uid["prometheus"]
    assert datasource["type"] == "prometheus"
    assert datasource["url"] == "http://prometheus:9090"
    assert datasource.get("access") == "proxy"
    assert datasource.get("isDefault") is True


def test_dashboards_reference_defined_datasource_uids() -> None:
    """Any datasource UID a dashboard uses must be provisioned."""
    defined = {datasource.get("uid") for datasource in _datasources()}
    for path in sorted(GRAFANA_DIR.rglob("*.json")):
        with path.open(encoding="utf-8") as handle:
            dashboard = json.load(handle)
        referenced = _dashboard_datasource_uids(dashboard)
        assert referenced <= defined, (
            f"{path.relative_to(REPO_ROOT)} references undefined UIDs: "
            f"{sorted(referenced - defined)}"
        )


def test_grafana_admin_password_comes_from_env() -> None:
    """The admin password is injected, not hardcoded in compose."""
    grafana = _load_compose()["services"]["grafana"]
    password = grafana["environment"]["GF_SECURITY_ADMIN_PASSWORD"]
    assert "${GF_ADMIN_PASSWORD" in password
