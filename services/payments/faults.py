"""Payment-service fault scenarios (M3.4)."""

import random
import time
from collections.abc import Mapping
from typing import Any

from common.faults import FaultRequest, register_fault
from common.logging import get_logger

_logger = get_logger(__name__)

_PROCESS_METHOD = "POST"
_PROCESS_PATH = "/payments/process"
_DEFAULT_MIN_SECONDS = 2.0
_DEFAULT_MAX_SECONDS = 5.0


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
