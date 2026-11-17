"""Tests for the Alertmanager webhook receiver (M3.8)."""

from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from webhook_receiver import main as receiver

client = TestClient(receiver.app)

PAYLOAD: dict[str, Any] = {
    "version": "4",
    "status": "firing",
    "receiver": "webhook",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "HighErrorRate", "service_name": "payments"},
            "annotations": {"summary": "High 5xx error rate on payments"},
        }
    ],
}


class _RecordingLogger:
    """Stand-in logger that records calls instead of writing to stdout."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        """Record one structured log event."""
        self.calls.append((event, fields))


def test_webhook_receiver_accepts_alertmanager_payload() -> None:
    """Alertmanager treats a 2xx response as successful delivery."""
    response = client.post("/webhook", json=PAYLOAD)

    assert response.status_code == 200


def test_webhook_receiver_logs_the_alert_payload(monkeypatch: MonkeyPatch) -> None:
    """The received status and alerts are logged as one structured event."""
    recorder = _RecordingLogger()
    monkeypatch.setattr(receiver, "_logger", recorder)

    client.post("/webhook", json=PAYLOAD)

    assert recorder.calls, "the receiver logged nothing"
    event, fields = recorder.calls[-1]
    assert event == "alert_received"
    assert fields["status"] == "firing"
    assert fields["alerts"][0]["labels"]["alertname"] == "HighErrorRate"
