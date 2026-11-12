"""Orders service"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

from common.logging import get_logger
from common.telemetry import init_observability

# Where the payments service lives. Compose sets this; the default matches its service name.
PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://payments:8002")

# Hard-coded for Milestone 1 — a real store is out of scope.
ORDERS: dict[str, dict[str, str | float]] = {
    "order-1": {"id": "order-1", "item": "keyboard", "status": "created", "amount": 89.99},
    "order-2": {"id": "order-2", "item": "monitor", "status": "shipped", "amount": 249.0},
}

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Keep one HTTP client for the whole process."""
    with httpx.Client(base_url=PAYMENTS_URL) as client:
        app.state.payments_client = client
        yield


app = FastAPI(title="orders", lifespan=lifespan)
init_observability(app, "orders")


def get_payments_client(request: Request) -> httpx.Client:
    """Return the lifespan-owned client used to call payments."""
    return request.app.state.payments_client


PaymentsClient = Annotated[httpx.Client, Depends(get_payments_client)]


def _with_payment(client: httpx.Client, order: dict[str, str | float]) -> dict[str, object]:
    """Process one payment for the order and attach the confirmation to it."""
    try:
        response = client.post(
            "/payments/process",
            json={"order_id": order["id"], "amount": order["amount"]},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("payments_call_failed", order_id=order["id"], error=str(exc))
        raise HTTPException(status_code=502, detail="payments service unavailable") from exc
    return {**order, "payment": response.json()}


@app.get("/orders")
def list_orders(client: PaymentsClient) -> list[dict[str, object]]:
    """Return every order with a freshly processed payment."""
    return [_with_payment(client, order) for order in ORDERS.values()]


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> dict[str, str | float]:
    """Return one order, or 404 when the id is unknown."""
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
