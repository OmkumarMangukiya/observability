"""Shared pytest setup.

Environment variables are forced here, at import time, before any application
module is imported. That keeps tests offline (no OTLP export) and deterministic
regardless of what is set in the developer's shell or .env file.
"""

import os

os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "debug"
# Resource names must come from the code argument, not the developer's shell.
os.environ.pop("OTEL_SERVICE_NAME", None)

# Fault injection is off unless a test turns it on.
for _key in [key for key in os.environ if key.startswith("FAULT_")]:
    os.environ.pop(_key)
