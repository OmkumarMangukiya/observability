"""Orders service (M1.5)."""

from fastapi import FastAPI, HTTPException

from common.telemetry import init_observability

# Hard-coded for Milestone 1 — a real store is out of scope.
ORDERS: dict[str, dict[str, str]] = {
    "order-1": {"id": "order-1", "item": "keyboard", "status": "created"},
    "order-2": {"id": "order-2", "item": "monitor", "status": "shipped"},
}

app = FastAPI(title="orders")
init_observability(app, "orders")


@app.get("/orders")
def list_orders() -> list[dict[str, str]]:
    """Return every order."""
    return list(ORDERS.values())


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> dict[str, str]:
    """Return one order, or 404 when the id is unknown."""
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
