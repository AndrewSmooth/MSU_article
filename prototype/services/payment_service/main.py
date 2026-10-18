from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI
from pydantic import BaseModel, Field

from common.database import create_pool
from common.observability import configure_tracing


class PaymentRequest(BaseModel):
    order_id: UUID
    amount: Decimal = Field(gt=0, decimal_places=2)


class PaymentResponse(BaseModel):
    payment_id: UUID
    order_id: UUID
    status: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    yield
    await app.state.pool.close()


app = FastAPI(title="payment-service", lifespan=lifespan)
configure_tracing(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/payments", response_model=PaymentResponse)
async def create_payment(payload: PaymentRequest) -> PaymentResponse:
    payment_id = uuid4()
    pool: asyncpg.Pool = app.state.pool
    await pool.execute(
        "INSERT INTO payments(id, order_id, amount) VALUES($1, $2, $3)",
        payment_id,
        payload.order_id,
        payload.amount,
    )
    return PaymentResponse(
        payment_id=payment_id,
        order_id=payload.order_id,
        status="captured",
    )

