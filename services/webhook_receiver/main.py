"""Minimal Alertmanager webhook receiver (M3.8)."""

from typing import Any

from fastapi import FastAPI

from common.logging import configure_logging, get_logger

configure_logging("webhook-receiver")
_logger = get_logger(__name__)

app = FastAPI(title="webhook-receiver")


@app.post("/webhook")
async def receive_alert(payload: dict[str, Any]) -> dict[str, str]:
    """Log one Alertmanager notification as structured JSON."""
    _logger.info(
        "alert_received",
        status=payload.get("status"),
        receiver=payload.get("receiver"),
        alerts=payload.get("alerts", []),
    )
    return {"status": "received"}
