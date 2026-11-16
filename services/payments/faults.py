"""Payment-service fault scenarios (M3.4, M3.5)."""

import random
import time
from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse, Response

from common.faults import FaultRequest, register_fault
from common.logging import get_logger

_logger = get_logger(__name__)

_PROCESS_METHOD = "POST"
_PROCESS_PATH = "/payments/process"
_DEFAULT_MIN_SECONDS = 2.0
_DEFAULT_MAX_SECONDS = 5.0
_DEFAULT_ERROR_RATE = 0.5


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
