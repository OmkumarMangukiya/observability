"""Gateway service (M1.6)."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from common.logging import get_logger
from common.telemetry import init_observability

# Where the orders service lives. Compose sets this; the default matches its service name.
ORDERS_URL = os.environ.get("ORDERS_URL", "http://orders:8001")

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Keep one HTTP client for the whole process."""
    with httpx.Client(base_url=ORDERS_URL) as client:
        app.state.orders_client = client
        yield


app = FastAPI(title="gateway", lifespan=lifespan)
init_observability(app, "gateway")


def get_orders_client(request: Request) -> httpx.Client:
    """Return the lifespan-owned client used to call orders."""
    return request.app.state.orders_client


OrdersClient = Annotated[httpx.Client, Depends(get_orders_client)]


def _forward(client: httpx.Client, path: str) -> JSONResponse:
    """Call orders and mirror its status and JSON body, or answer 502 on no answer."""
    try:
        response = client.get(path)
    except httpx.TransportError as exc:
        logger.error("orders_call_failed", path=path, error=str(exc))
        return JSONResponse(status_code=502, content={"detail": "orders service unavailable"})
    return JSONResponse(status_code=response.status_code, content=response.json())


@app.get("/orders")
def list_orders(client: OrdersClient) -> JSONResponse:
    """Proxy the orders list."""
    return _forward(client, "/orders")


@app.get("/orders/{order_id}")
def get_order(order_id: str, client: OrdersClient) -> JSONResponse:
    """Proxy one order, including the orders service's 404."""
    return _forward(client, f"/orders/{order_id}")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
