"""Payments service (M3.1)."""

import random
import time
from itertools import count

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import payments.faults  # noqa: F401  -- registers fault types before init_faults()
from common.faults import init_faults
from common.telemetry import init_observability

# Hard-coded in-memory store — persistence is out of scope.
PAYMENTS: dict[str, dict[str, str | float]] = {}
_payment_ids = count(1)

# Simulated processing time, in seconds.
_MIN_PROCESSING_SECONDS = 0.05
_MAX_PROCESSING_SECONDS = 0.2

app = FastAPI(title="payments")
init_faults(app)
init_observability(app, "payments")


class PaymentRequest(BaseModel):
    """Body of POST /payments/process."""

    order_id: str
    amount: float


@app.post("/payments/process")
def process_payment(body: PaymentRequest) -> dict[str, str | float]:
    """Charge the order with a simulated 50-200 ms processing delay."""
    time.sleep(random.uniform(_MIN_PROCESSING_SECONDS, _MAX_PROCESSING_SECONDS))
    payment_id = f"payment-{next(_payment_ids)}"
    payment: dict[str, str | float] = {
        "id": payment_id,
        "order_id": body.order_id,
        "amount": body.amount,
        "status": "confirmed",
    }
    PAYMENTS[payment_id] = payment
    return payment


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str) -> dict[str, str | float]:
    """Return one payment, or 404 when the id is unknown."""
    payment = PAYMENTS.get(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return payment


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
