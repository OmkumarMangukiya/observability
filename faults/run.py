"""Drill runner: enable a fault, drive traffic, optionally wait for the alert (M4.1).

This is scenario control and traffic only. It never queries metrics, logs or
traces — finding the cause stays a manual exercise, which is the point of Phase 4.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Sequence
from typing import Any

from faults import (
    SERVICE_DEFAULT_URLS,
    SERVICE_URL_ENV,
    CatalogError,
    Scenario,
    Traffic,
    get_scenario,
    load_catalog,
)

_CONTROL_TIMEOUT_SECONDS = 10.0
# Long enough to see the gateway's own 15s timeout surface as a 504.
_TRAFFIC_TIMEOUT_SECONDS = 30.0
_DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
_DEFAULT_ALERT_TIMEOUT_SECONDS = 180.0
_ALERT_POLL_SECONDS = 5.0


class RunnerError(RuntimeError):
    """A runner step could not reach or understand a service."""


def service_url(service: str) -> str:
    """Return a service's host URL, honoring its <SERVICE>_URL environment override."""
    if service not in SERVICE_URL_ENV:
        raise CatalogError(f"unknown service {service!r}")
    return os.environ.get(SERVICE_URL_ENV[service], SERVICE_DEFAULT_URLS[service]).rstrip("/")


def prometheus_url() -> str:
    """Return the Prometheus URL, honoring PROMETHEUS_URL."""
    return os.environ.get("PROMETHEUS_URL", _DEFAULT_PROMETHEUS_URL).rstrip("/")


def _base_url(service: str, override: str | None = None) -> str:
    """Return the target base URL, preferring an explicit override."""
    if override:
        return override.rstrip("/")
    return service_url(service)


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = _CONTROL_TIMEOUT_SECONDS,
) -> Any:
    """Send one JSON request and parse the JSON reply."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RunnerError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RunnerError(f"{method} {url} failed: {exc.reason}") from exc

    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"{method} {url} returned invalid JSON") from exc


def _fault_listing(raw: Any, url: str) -> list[dict[str, Any]]:
    """Check that a /faults reply is a list of fault objects."""
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise RunnerError(f"{url} returned an unexpected /faults body")
    return raw


def set_fault(
    scenario: Scenario, *, enabled: bool, base_url: str | None = None
) -> list[dict[str, Any]]:
    """Turn a scenario's fault on or off and return the service's active faults."""
    url = f"{_base_url(scenario.service, base_url)}/faults"
    payload: dict[str, Any] = {
        "fault": scenario.fault,
        "enabled": enabled,
        "params": dict(scenario.params) if enabled else {},
    }
    return _fault_listing(_request_json("POST", url, payload), url)


def enable(scenario: Scenario, base_url: str | None = None) -> list[dict[str, Any]]:
    """Enable a scenario's fault; repeating the call is harmless and idempotent."""
    return set_fault(scenario, enabled=True, base_url=base_url)


def disable(scenario: Scenario, base_url: str | None = None) -> list[dict[str, Any]]:
    """Disable a scenario's fault; repeating the call is harmless and idempotent."""
    return set_fault(scenario, enabled=False, base_url=base_url)


def traffic_url(traffic: Traffic) -> str:
    """Return the absolute URL the traffic targets."""
    return service_url(traffic.service) + traffic.path


def _send_traffic(method: str, url: str) -> int:
    """Send one request and return its status; 0 means it never got a reply.

    Drill failures are evidence, not runner errors, so every outcome is returned.
    """
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_TRAFFIC_TIMEOUT_SECONDS) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code
    except urllib.error.URLError:
        return 0


def drive_traffic(traffic: Traffic) -> list[int]:
    """Send the catalog's requests, spaced by the catalog interval."""
    url = traffic_url(traffic)
    statuses: list[int] = []
    for index in range(traffic.requests):
        statuses.append(_send_traffic(traffic.method, url))
        if index + 1 < traffic.requests and traffic.interval_seconds:
            time.sleep(traffic.interval_seconds)
    return statuses


def firing_alerts(prometheus: str) -> set[str]:
    """Return the names of alerts Prometheus currently reports as firing."""
    url = f"{prometheus.rstrip('/')}/api/v1/alerts"
    payload = _request_json("GET", url)
    if not isinstance(payload, dict):
        raise RunnerError(f"GET {url} returned an unexpected body")

    data = payload.get("data")
    alerts = data.get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, list):
        return set()

    names: set[str] = set()
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get("state") != "firing":
            continue
        labels = alert.get("labels")
        name = labels.get("alertname") if isinstance(labels, dict) else None
        if isinstance(name, str):
            names.add(name)
    return names


def wait_for_alert(
    alert_name: str,
    *,
    traffic: Traffic,
    prometheus: str,
    timeout_seconds: float = _DEFAULT_ALERT_TIMEOUT_SECONDS,
) -> bool:
    """Poll Prometheus until the alert fires, keeping traffic flowing.

    The alert rules use 5-minute rate windows and a 1-minute `for`, so the
    requests have to keep arriving while we wait.
    """
    url = traffic_url(traffic)
    deadline = time.monotonic() + timeout_seconds
    while True:
        if alert_name in firing_alerts(prometheus):
            return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        _send_traffic(traffic.method, url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_ALERT_POLL_SECONDS, remaining))


def _print_active(
    scenario: Scenario, faults: list[dict[str, Any]], base_url: str | None = None
) -> None:
    """Show what the target service says is active."""
    target = _base_url(scenario.service, base_url)
    if not faults:
        print(f"{target}/faults: no active faults")
        return
    shown = ", ".join(
        f"{fault.get('fault')} {json.dumps(fault.get('params', {}), sort_keys=True)}"
        for fault in faults
    )
    print(f"{target}/faults: {shown}")


def print_scenarios(scenarios: list[Scenario]) -> None:
    """Print every drill in the catalog."""
    print(f"{len(scenarios)} drill scenarios")
    for scenario in scenarios:
        traffic = scenario.traffic
        print()
        print(scenario.id)
        params = json.dumps(scenario.params, sort_keys=True)
        print(f"  fault:   {scenario.service}/{scenario.fault} params={params}")
        print(
            f"  traffic: {traffic.requests}x {traffic.method} {traffic_url(traffic)} "
            f"every {traffic.interval_seconds:g}s"
        )
        print(f"  symptom: {scenario.expected_symptom}")
        print(f"  alert:   {scenario.expected_alert or 'none (find it manually)'}")


def run_scenario(
    scenario: Scenario,
    *,
    base_url: str | None = None,
    no_disable: bool = False,
    wait_alert: bool = False,
    alert_timeout: float = _DEFAULT_ALERT_TIMEOUT_SECONDS,
    prometheus: str | None = None,
) -> int:
    """Enable, drive traffic, optionally wait for the alert, then disable."""
    target = _base_url(scenario.service, base_url)
    traffic = scenario.traffic
    print(f"scenario: {scenario.id} ({scenario.service}/{scenario.fault})")
    print(
        f"traffic:  {traffic.requests}x {traffic.method} {traffic_url(traffic)} "
        f"every {traffic.interval_seconds:g}s"
    )

    enable(scenario, base_url=target)
    print(f"fault on: {target}/faults reports {scenario.fault}")

    alert_fired: bool | None = None
    try:
        statuses = drive_traffic(traffic)
        print(f"traffic done: {dict(sorted(Counter(statuses).items()))}")

        if wait_alert:
            if scenario.expected_alert is None:
                print("alert wait: skipped — this scenario raises no alert")
            else:
                endpoint = prometheus or prometheus_url()
                print(
                    f"alert wait: polling {endpoint} for {scenario.expected_alert} "
                    f"(up to {alert_timeout:g}s)"
                )
                alert_fired = wait_for_alert(
                    scenario.expected_alert,
                    traffic=traffic,
                    prometheus=endpoint,
                    timeout_seconds=alert_timeout,
                )
                if alert_fired:
                    print(f"alert: {scenario.expected_alert} is firing")
                else:
                    print(f"alert: {scenario.expected_alert} did not fire in {alert_timeout:g}s")
    finally:
        if no_disable:
            print(f"fault stays on: disable with `python -m faults.run disable {scenario.id}`")
        else:
            disable(scenario, base_url=target)
            print("fault off")

    return 1 if alert_fired is False else 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python -m faults.run",
        description="Run fault-drill scenarios against the local stack.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="show every drill scenario")

    for command, help_text in (
        ("enable", "turn a scenario's fault on"),
        ("disable", "turn a scenario's fault off"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("scenario", help="scenario id (see `list`)")
        command_parser.add_argument(
            "--base-url",
            help="override the target service URL (for example, a remote payments)",
        )

    run_parser = subparsers.add_parser("run", help="enable, drive traffic, then disable")
    run_parser.add_argument("scenario", help="scenario id (see `list`)")
    run_parser.add_argument(
        "--base-url",
        help="override the target service URL (for example, a remote payments)",
    )
    run_parser.add_argument(
        "--no-disable",
        action="store_true",
        help="leave the fault on so you can investigate in Grafana",
    )
    run_parser.add_argument(
        "--wait-alert",
        action="store_true",
        help="poll Prometheus until the scenario's expected alert fires",
    )
    run_parser.add_argument(
        "--alert-timeout",
        type=float,
        default=_DEFAULT_ALERT_TIMEOUT_SECONDS,
        help=f"seconds to wait for the alert (default: {_DEFAULT_ALERT_TIMEOUT_SECONDS:g})",
    )
    run_parser.add_argument(
        "--prometheus-url",
        help="Prometheus URL (default: PROMETHEUS_URL or http://localhost:9090)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run one command."""
    args = build_parser().parse_args(argv)

    try:
        scenarios = load_catalog()
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "list":
        print_scenarios(scenarios)
        return 0

    try:
        scenario = get_scenario(args.scenario, scenarios)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "enable":
            _print_active(scenario, enable(scenario, base_url=args.base_url), args.base_url)
            return 0
        if args.command == "disable":
            _print_active(scenario, disable(scenario, base_url=args.base_url), args.base_url)
            return 0
        return run_scenario(
            scenario,
            base_url=args.base_url,
            no_disable=args.no_disable,
            wait_alert=args.wait_alert,
            alert_timeout=args.alert_timeout,
            prometheus=args.prometheus_url,
        )
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
