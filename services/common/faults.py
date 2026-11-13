"""Fault-injection framework shared by every service (M3.3)."""

import asyncio
import json
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from common.logging import get_logger

_logger = get_logger(__name__)

_ENV_PREFIX = "FAULT_"
# A fault can break every request, but never the switch that turns it off.
_CONTROL_PATHS = frozenset({"/faults"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class FaultRequest:
    """The parts of an incoming request a fault handler may act on."""

    method: str
    path: str


FaultHandler = Callable[[FaultRequest, Mapping[str, Any]], Response | None]


@dataclass(frozen=True)
class Fault:
    """A registered fault type."""

    name: str
    handler: FaultHandler
    env_param: str
    description: str


_registry: dict[str, Fault] = {}
_active: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def register_fault(
    name: str, *, env_param: str = "value", description: str = ""
) -> Callable[[FaultHandler], FaultHandler]:
    """Register a fault type; the decorated function handles matching requests.

    Returning a Response injects it and skips the route. Returning None lets the
    request continue, which is how a delay fault works.
    """

    def decorator(handler: FaultHandler) -> FaultHandler:
        with _lock:
            if name in _registry:
                raise ValueError(f"fault {name!r} is already registered")
            _registry[name] = Fault(name, handler, env_param, description)
        return handler

    return decorator


def activate(name: str, params: Mapping[str, Any] | None = None) -> None:
    """Turn one registered fault on with the given parameters."""
    with _lock:
        if name not in _registry:
            raise KeyError(f"unknown fault {name!r}")
        _active[name] = dict(params or {})


def deactivate(name: str) -> None:
    """Turn one fault off; unknown or already-inactive names are ignored."""
    with _lock:
        _active.pop(name, None)


def clear_faults() -> None:
    """Turn every fault off."""
    with _lock:
        _active.clear()


def active_faults() -> dict[str, dict[str, Any]]:
    """Return a copy of the active faults and their parameters."""
    with _lock:
        return {name: dict(params) for name, params in _active.items()}


def _snapshot() -> list[tuple[Fault, dict[str, Any]]]:
    """Return the active faults with their params, taken under one lock."""
    with _lock:
        return [(_registry[name], dict(params)) for name, params in _active.items()]


def _parse_env_value(raw: str, fault: Fault) -> dict[str, Any] | None:
    """Turn a FAULT_* value into params, or None when the fault is off."""
    text = raw.strip()
    lowered = text.lower()
    if lowered in _FALSE_VALUES:
        return None
    if lowered in _TRUE_VALUES:
        return {}
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        parsed = text
    if isinstance(parsed, dict):
        return dict(parsed)
    return {fault.env_param: parsed}


def load_from_env(environ: Mapping[str, str] | None = None) -> None:
    """Apply FAULT_* environment variables to the active faults.

    Unknown names are logged and skipped, so a typo cannot stop a service from
    starting.
    """
    source = os.environ if environ is None else environ
    with _lock:
        faults = list(_registry.values())

    for fault in faults:
        raw = source.get(f"{_ENV_PREFIX}{fault.name.upper()}")
        if raw is None:
            continue
        params = _parse_env_value(raw, fault)
        with _lock:
            if params is None:
                _active.pop(fault.name, None)
            else:
                _active[fault.name] = params

    known = {f"{_ENV_PREFIX}{fault.name.upper()}" for fault in faults}
    for key in source:
        if key.startswith(_ENV_PREFIX) and key not in known:
            _logger.warning("unknown_fault_env_var", variable=key)


class FaultMiddleware:
    """Run active faults in front of the routes, one by one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # A mounted /faults redirects to /faults/, so ignore the trailing slash.
        path = scope.get("path", "").rstrip("/")
        active = _snapshot() if scope["type"] == "http" else []
        if not active or path in _CONTROL_PATHS:
            await self.app(scope, receive, send)
            return

        request = FaultRequest(method=scope.get("method", ""), path=path)
        for fault, params in active:
            # Handlers may sleep, so keep them off the event loop.
            response = await asyncio.to_thread(fault.handler, request, params)
            if response is not None:
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


class FaultToggle(BaseModel):
    """Body of POST /faults."""

    fault: str
    enabled: bool
    params: dict[str, Any] = Field(default_factory=dict)


router = APIRouter()


@router.get("/faults")
def list_faults() -> list[dict[str, Any]]:
    """List the faults that are currently active."""
    return [
        {"fault": name, "enabled": True, "params": params}
        for name, params in active_faults().items()
    ]


@router.post("/faults")
def toggle_fault(body: FaultToggle) -> list[dict[str, Any]]:
    """Turn one fault on or off; an unknown name is a 400."""
    if body.fault not in _registry:
        raise HTTPException(status_code=400, detail=f"unknown fault: {body.fault}")
    if body.enabled:
        activate(body.fault, body.params)
    else:
        deactivate(body.fault)
    return list_faults()


def init_faults(app: FastAPI) -> None:
    """Mount the /faults endpoints and fault middleware on a service.

    Call this before init_observability: the metrics and tracing middleware
    must wrap it, so injected responses and delays are still measured.
    """
    if getattr(app.state, "faults_initialized", False):
        return
    load_from_env()
    app.add_middleware(FaultMiddleware)
    app.include_router(router)
    app.state.faults_initialized = True
