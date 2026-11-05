"""Tests for structured logging (M1.2)."""

import json
from datetime import datetime, timedelta

from opentelemetry.sdk.trace import TracerProvider

from common.logging import configure_logging, get_logger


def _logged_events(captured: str) -> list[dict[str, object]]:
    """Parse captured stdout into one dict per JSON log line."""
    return [json.loads(line) for line in captured.splitlines() if line.strip()]


def test_log_line_is_json_with_required_fields(capsys) -> None:
    """Every event is one JSON object with the shared fields."""
    configure_logging("orders")

    get_logger().info("order_created", order_id="order-1")
    captured = capsys.readouterr().out

    events = _logged_events(captured)
    assert len(events) == 1
    event = events[0]

    assert event["event"] == "order_created"
    assert event["level"] == "info"
    assert event["service"] == "orders"
    assert event["order_id"] == "order-1"

    timestamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
    assert timestamp.utcoffset() == timedelta(0)


def test_ids_are_none_outside_a_span(capsys) -> None:
    """There is no active span, so both IDs are JSON null."""
    configure_logging("orders")

    get_logger().info("no_span_here")
    captured = capsys.readouterr().out

    event = _logged_events(captured)[0]
    assert event["trace_id"] is None
    assert event["span_id"] is None


def test_ids_come_from_the_active_span(capsys) -> None:
    """Inside a span the IDs are the span's own, zero-padded to 32/16 hex."""
    configure_logging("orders")
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("checkout") as span:
        get_logger().info("inside_span")
        span_context = span.get_span_context()

    captured = capsys.readouterr().out
    event = _logged_events(captured)[0]

    assert event["trace_id"] == f"{span_context.trace_id:032x}"
    assert event["span_id"] == f"{span_context.span_id:016x}"


def test_configure_logging_only_applies_once(capsys) -> None:
    """Re-configuring must not duplicate setup or change the service."""
    configure_logging("orders")
    configure_logging("gateway")

    get_logger().info("still_orders")
    captured = capsys.readouterr().out

    events = _logged_events(captured)
    assert len(events) == 1
    assert events[0]["service"] == "orders"
