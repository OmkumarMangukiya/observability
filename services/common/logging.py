"""Structured JSON logging shared by every service (M1.2)."""

from collections.abc import Callable
from typing import Any

import structlog
from opentelemetry import trace

EventDict = dict[str, Any]
"""
Callable[[Input Arguments],Return Value]
Any -> can be any object, usually the logger instance
str -> the name of the log method
EventDict -> Dict containing the log data
return value -> updated even dict
"""
Processor = Callable[[Any, str, EventDict], EventDict]

_configured = False


def _add_service(service: str) -> Processor:
    """Return a processor that stamps every event with the service name."""

    def processor(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = service
        return event_dict

    return processor


def _add_trace_context(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Add the active span's trace and span IDs, or None when outside a span."""
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = f"{span_context.trace_id:032x}"
        event_dict["span_id"] = f"{span_context.span_id:016x}"
    else:
        event_dict["trace_id"] = None
        event_dict["span_id"] = None
    return event_dict


def configure_logging(service: str) -> None:
    """Configure JSON logging to stdout once per process."""
    global _configured
    if _configured:
        return

    structlog.configure(
        processors=[
            _add_service(service),
            _add_trace_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Return a structlog logger; call configure_logging first."""
    return structlog.get_logger(name)
