"""Tests for infra configuration files (M1.8, M1.9, M1.10, M1.11)."""

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
PROMETHEUS_CONFIG_PATH = REPO_ROOT / "infra" / "prometheus" / "prometheus.yml"
LOKI_CONFIG_PATH = REPO_ROOT / "infra" / "loki" / "loki-config.yml"
TEMPO_CONFIG_PATH = REPO_ROOT / "infra" / "tempo" / "tempo.yml"
ALLOY_CONFIG_PATH = REPO_ROOT / "infra" / "alloy" / "config.alloy"
DATASOURCES_PATH = (
    REPO_ROOT / "infra" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
)
DASHBOARDS_PROVISIONING_PATH = (
    REPO_ROOT / "infra" / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
)
DASHBOARD_PATH = REPO_ROOT / "infra" / "grafana" / "dashboards" / "milestone-1.json"
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


def _alloy_config() -> str:
    """Return the Alloy config with whitespace collapsed to single spaces."""
    assert ALLOY_CONFIG_PATH.is_file(), (
        f"missing config file: {ALLOY_CONFIG_PATH.relative_to(REPO_ROOT)}"
    )
    return " ".join(ALLOY_CONFIG_PATH.read_text(encoding="utf-8").split())


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
    for path in (
        COMPOSE_PATH,
        PROMETHEUS_CONFIG_PATH,
        LOKI_CONFIG_PATH,
        TEMPO_CONFIG_PATH,
        DATASOURCES_PATH,
    ):
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


def test_loki_config_uses_single_binary_filesystem_storage() -> None:
    """Loki runs single-binary: no auth, schema v13, filesystem storage."""
    config = _load_yaml(LOKI_CONFIG_PATH)
    assert config["auth_enabled"] is False

    schema = config["schema_config"]["configs"][0]
    assert schema["schema"] == "v13"
    assert schema["store"] == "tsdb"
    assert schema["object_store"] == "filesystem"

    storage = config["common"]["storage"]["filesystem"]
    assert storage["chunks_directory"] == "/loki/chunks"
    assert storage["rules_directory"] == "/loki/rules"

    assert config["limits_config"]["allow_structured_metadata"] is True
    assert config["analytics"]["reporting_enabled"] is False


def test_loki_is_published_for_the_smoke_script() -> None:
    """Loki's port 3100 is published so smoke.sh can query it from the host."""
    loki = _load_compose()["services"]["loki"]
    assert "3100" in _container_ports(loki)


def test_alloy_ships_app_container_logs_to_loki() -> None:
    """Alloy reads Docker logs, keeps gateway/orders, and pushes to Loki."""
    config = _alloy_config()
    assert 'discovery.docker "containers"' in config
    assert 'discovery.relabel "services"' in config
    assert "targets = discovery.docker.containers.targets" in config

    assert 'loki.source.docker "app_logs"' in config
    assert "targets = discovery.relabel.services.output" in config

    assert "__meta_docker_container_label_com_docker_compose_service" in config
    assert 'regex = "gateway|orders"' in config
    assert 'target_label = "service_name"' in config

    assert 'loki.write "local"' in config
    assert 'url = "http://loki:3100/loki/api/v1/push"' in config
    assert "forward_to = [loki.write.local.receiver]" in config


def test_alloy_and_loki_mount_their_configs() -> None:
    """Loki and Alloy mount their configs; Alloy also gets the Docker socket."""
    services = _load_compose()["services"]
    loki_volumes = [str(volume) for volume in services["loki"]["volumes"]]
    alloy_volumes = [str(volume) for volume in services["alloy"]["volumes"]]

    assert "./infra/loki/loki-config.yml:/etc/loki/loki-config.yml:ro" in loki_volumes
    assert "./infra/alloy/config.alloy:/etc/alloy/config.alloy:ro" in alloy_volumes
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in alloy_volumes

    assert LOKI_CONFIG_PATH.is_file()
    assert ALLOY_CONFIG_PATH.is_file()


def test_tempo_config_uses_single_binary_local_storage() -> None:
    """Tempo stores traces on local disk and accepts OTLP on 4317/4318."""
    config = _load_yaml(TEMPO_CONFIG_PATH)
    assert config["server"]["http_listen_port"] == 3200

    protocols = config["distributor"]["receivers"]["otlp"]["protocols"]
    assert protocols["grpc"]["endpoint"] == "0.0.0.0:4317"
    assert protocols["http"]["endpoint"] == "0.0.0.0:4318"

    trace_storage = config["storage"]["trace"]
    assert trace_storage["backend"] == "local"
    assert trace_storage["wal"]["path"].startswith("/var/tempo/")
    assert trace_storage["local"]["path"].startswith("/var/tempo/")


def test_tempo_is_published_for_the_smoke_script() -> None:
    """Tempo's port 3200 is published so smoke.sh can query it from the host."""
    tempo = _load_compose()["services"]["tempo"]
    assert "3200" in _container_ports(tempo)


def test_tempo_mounts_config_and_data_volume() -> None:
    """Tempo mounts its config read-only and keeps data in a named volume."""
    services = _load_compose()["services"]
    volumes = [str(volume) for volume in services["tempo"]["volumes"]]
    assert "./infra/tempo/tempo.yml:/etc/tempo/tempo.yml:ro" in volumes
    assert "tempo-data:/var/tempo" in volumes
    assert TEMPO_CONFIG_PATH.is_file()


def test_alloy_receives_otlp_and_exports_to_tempo() -> None:
    """Alloy accepts spans over OTLP and batches them to Tempo."""
    config = _alloy_config()
    assert 'otelcol.receiver.otlp "traces"' in config
    assert 'endpoint = "0.0.0.0:4317"' in config
    assert 'endpoint = "0.0.0.0:4318"' in config
    assert 'otelcol.processor.batch "default"' in config
    assert 'otelcol.exporter.otlp "tempo"' in config
    assert 'endpoint = "tempo:4317"' in config
    assert "insecure = true" in config

    assert "traces = [otelcol.processor.batch.default.input]" in config
    assert "traces = [otelcol.exporter.otlp.tempo.input]" in config


def test_alloy_runs_at_experimental_stability() -> None:
    """The otelcol.* components need Alloy's experimental stability level."""
    command = [str(part) for part in _load_compose()["services"]["alloy"]["command"]]
    assert "--stability.level=experimental" in command


def test_services_export_traces_to_alloy() -> None:
    """Both services send OTLP/HTTP spans to Alloy and tag the environment."""
    services = _load_compose()["services"]
    for name in ("gateway", "orders"):
        environment = services[name]["environment"]
        assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://alloy:4318"
        assert "deployment.environment=" in environment["OTEL_RESOURCE_ATTRIBUTES"]


def test_grafana_prometheus_datasource_is_provisioned() -> None:
    """Prometheus is provisioned with the fixed UID, proxied, and default."""
    by_uid = {datasource.get("uid"): datasource for datasource in _datasources()}
    assert "prometheus" in by_uid, "datasource UID 'prometheus' is missing"
    datasource = by_uid["prometheus"]
    assert datasource["type"] == "prometheus"
    assert datasource["url"] == "http://prometheus:9090"
    assert datasource.get("access") == "proxy"
    assert datasource.get("isDefault") is True


def test_grafana_loki_datasource_is_provisioned() -> None:
    """Loki is provisioned with UID 'loki', proxied to the Loki container."""
    by_uid = {datasource.get("uid"): datasource for datasource in _datasources()}
    assert "loki" in by_uid, "datasource UID 'loki' is missing"
    datasource = by_uid["loki"]
    assert datasource["type"] == "loki"
    assert datasource["url"] == "http://loki:3100"
    assert datasource.get("access") == "proxy"


def test_grafana_tempo_datasource_is_provisioned() -> None:
    """Tempo is provisioned with UID 'tempo', proxied to the Tempo container."""
    by_uid = {datasource.get("uid"): datasource for datasource in _datasources()}
    assert "tempo" in by_uid, "datasource UID 'tempo' is missing"
    datasource = by_uid["tempo"]
    assert datasource["type"] == "tempo"
    assert datasource["url"] == "http://tempo:3200"
    assert datasource.get("access") == "proxy"


def test_loki_logs_link_to_tempo_traces() -> None:
    """A log line's trace_id links to the same trace in Tempo."""
    by_uid = {datasource.get("uid"): datasource for datasource in _datasources()}
    derived = by_uid["loki"]["jsonData"]["derivedFields"]
    to_tempo = [field for field in derived if field.get("datasourceUid") == "tempo"]
    assert to_tempo, "Loki has no derived field pointing at Tempo"
    field = to_tempo[0]
    assert "trace_id" in field["matcherRegex"]
    assert field["url"] == "$${__value.raw}"


def test_tempo_traces_link_to_loki_logs() -> None:
    """Tempo's 'Logs for this span' filters Loki by trace ID."""
    by_uid = {datasource.get("uid"): datasource for datasource in _datasources()}
    traces_to_logs = by_uid["tempo"]["jsonData"]["tracesToLogsV2"]
    assert traces_to_logs["datasourceUid"] == "loki"
    assert traces_to_logs["filterByTraceID"] is True


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


def test_dashboards_are_provisioned_from_the_mounted_folder() -> None:
    """dashboards.yml points inside the container at the mounted dashboard folder."""
    providers = _load_yaml(DASHBOARDS_PROVISIONING_PATH)["providers"]
    assert providers, "dashboards.yml declares no providers"
    provider = providers[0]
    assert provider["folder"] == "Demo"
    provider_path = provider["options"]["path"]
    assert provider_path == "/var/lib/grafana/dashboards"

    volume = f"./infra/grafana/dashboards:{provider_path}:ro"
    grafana_volumes = [str(entry) for entry in _load_compose()["services"]["grafana"]["volumes"]]
    assert volume in grafana_volumes, f"grafana does not mount {volume}"
    assert DASHBOARD_PATH.is_file()


def test_milestone_1_dashboard_has_the_required_panels() -> None:
    """Dashboard is 15m/5s, has a $service variable and all six panels."""
    assert DASHBOARD_PATH.is_file(), "missing dashboard JSON"
    with DASHBOARD_PATH.open(encoding="utf-8") as handle:
        dashboard = json.load(handle)

    assert dashboard["uid"] == "milestone-1"
    assert dashboard["refresh"] == "5s"
    assert dashboard["time"] == {"from": "now-15m", "to": "now"}
    assert any(variable["name"] == "service" for variable in dashboard["templating"]["list"])

    panels = dashboard["panels"]
    assert len(panels) == 6
    assert {"logs", "traces"} <= {panel["type"] for panel in panels}

    exprs = [
        target["expr"]
        for panel in panels
        for target in panel.get("targets", [])
        if "expr" in target
    ]
    assert any("rate(http_server_requests_total" in expr for expr in exprs)
    assert any('http_response_status_code=~"5.."' in expr for expr in exprs)
    assert any("histogram_quantile(0.95" in expr for expr in exprs)
    assert any("histogram_quantile(0.99" in expr for expr in exprs)
    assert any('{service_name=~"$service"}' in expr for expr in exprs)

    queries = [target for panel in panels for target in panel.get("targets", [])]
    assert any(query.get("queryType") == "traceql" for query in queries)
