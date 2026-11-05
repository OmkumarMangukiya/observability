"""Smoke tests for the test harness itself (M1.1)."""

import os

import common
import gateway
import orders


def test_environment_is_isolated() -> None:
    """conftest.py wins over the shell environment before apps are imported."""
    assert os.environ["ENVIRONMENT"] == "test"
    assert os.environ["LOG_LEVEL"] == "debug"
    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == ""


def test_service_packages_import() -> None:
    """`pythonpath = ["services"]` makes the service packages importable."""
    assert common.__name__ == "common"
    assert gateway.__name__ == "gateway"
    assert orders.__name__ == "orders"
