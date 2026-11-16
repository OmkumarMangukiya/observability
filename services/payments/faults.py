"""Payment-service fault scenarios (M3.4, M3.5, M3.6, M3.7)."""

import os
import random
import time
from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse, Response

from common.faults import FaultRequest, active_faults, register_fault
from common.logging import get_logger

_logger = get_logger(__name__)

_PROCESS_METHOD = "POST"
_PROCESS_PATH = "/payments/process"
# Probes keep working under every fault, so the failure stays observable.
_INFRA_PATHS = frozenset({"/healthz", "/metrics"})
_DEFAULT_MIN_SECONDS = 2.0
_DEFAULT_MAX_SECONDS = 5.0
_DEFAULT_ERROR_RATE = 0.5
# Longer than any caller waits, so the caller always times out first.
_DEFAULT_HANG_SECONDS = 30.0
_DEFAULT_LEAK_BYTES = 1024 * 1024
_FAKE_GIT_SHA = "badc0de"

# Nothing ever removes from this list — that is the leak.
_leaked_memory: list[bytes] = []


def _delay_seconds(params: Mapping[str, Any]) -> float:
    """Pick the delay: an exact `seconds`, or a random min/max range."""
    if "seconds" in params:
        return max(0.0, float(params["seconds"]))

    low = float(params.get("min_seconds", _DEFAULT_MIN_SECONDS))
    high = float(params.get("max_seconds", _DEFAULT_MAX_SECONDS))
    low, high = min(low, high), max(low, high)
    return random.uniform(max(0.0, low), max(0.0, high))


@register_fault(
    "slow_endpoint",
    env_param="seconds",
    description="Add an artificial delay to POST /payments/process.",
)
def slow_endpoint(request: FaultRequest, params: Mapping[str, Any]) -> None:
    """Sleep before the real handler; returning None lets the request through."""
    if request.method != _PROCESS_METHOD or request.path != _PROCESS_PATH:
        return None

    delay = _delay_seconds(params)
    _logger.info(
        "fault_injected",
        fault="slow_endpoint",
        path=request.path,
        delay_seconds=round(delay, 3),
    )
    time.sleep(delay)
    return None


def _error_rate(params: Mapping[str, Any]) -> float:
    """Read the failure rate from params, clamped to 0.0-1.0."""
    try:
        rate = float(params.get("rate", _DEFAULT_ERROR_RATE))
    except (TypeError, ValueError):
        return _DEFAULT_ERROR_RATE
    return min(max(rate, 0.0), 1.0)


@register_fault(
    "random_500s",
    env_param="rate",
    description="Fail a share of POST /payments/process requests with a 500.",
)
def random_500s(request: FaultRequest, params: Mapping[str, Any]) -> Response | None:
    """Return a 500 for a random share of process requests, set by `rate`."""
    if request.method != _PROCESS_METHOD or request.path != _PROCESS_PATH:
        return None

    rate = _error_rate(params)
    if random.random() >= rate:
        return None

    _logger.info(
        "fault_injected",
        fault="random_500s",
        path=request.path,
        rate=rate,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "injected failure", "fault": "random_500s"},
    )


@register_fault(
    "dependency_timeout",
    env_param="seconds",
    description="Hang POST /payments/process past the caller's timeout.",
)
def dependency_timeout(request: FaultRequest, params: Mapping[str, Any]) -> None:
    """Sleep long enough that the caller gives up; the real handler never runs."""
    if request.method != _PROCESS_METHOD or request.path != _PROCESS_PATH:
        return None

    seconds = max(0.0, float(params.get("seconds", _DEFAULT_HANG_SECONDS)))
    _logger.info(
        "fault_injected",
        fault="dependency_timeout",
        path=request.path,
        delay_seconds=round(seconds, 3),
    )
    time.sleep(seconds)
    return None


@register_fault(
    "bad_deploy",
    env_param="enabled",
    description="Fail every application request with a 500 after a bad deploy.",
)
def bad_deploy(request: FaultRequest, params: Mapping[str, Any]) -> Response | None:
    """Return a 500 for every real request; infra probes are left alone."""
    if request.path in _INFRA_PATHS:
        return None

    _logger.info("fault_injected", fault="bad_deploy", path=request.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "bad deploy", "fault": "bad_deploy"},
    )


@register_fault(
    "memory_leak",
    env_param="bytes_per_request",
    description="Retain memory on every request so RSS climbs.",
)
def memory_leak(request: FaultRequest, params: Mapping[str, Any]) -> None:
    """Keep a chunk of bytes per request; nothing ever frees it."""
    if request.path in _INFRA_PATHS:
        return None

    size = max(0, int(params.get("bytes_per_request", _DEFAULT_LEAK_BYTES)))
    _leaked_memory.append(b"x" * size)
    _logger.info(
        "fault_injected",
        fault="memory_leak",
        path=request.path,
        bytes=size,
    )
    return None


def emit_deploy_marker() -> None:
    """Log the deploy marker at startup, but only for the bad-deploy scenario."""
    if "bad_deploy" not in active_faults():
        return

    _logger.info(
        "deploy",
        service="payments",
        git_sha=os.environ.get("GIT_SHA", _FAKE_GIT_SHA),
        fault="bad_deploy",
    )
