import json
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import httpx
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from pydantic import BaseModel, Field

from common.database import create_pool
from common.observability import configure_tracing


tracer = trace.get_tracer(__name__)


class OrderRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, decimal_places=2)


class OrderResponse(BaseModel):
    order_id: UUID
    payment_id: UUID
    status: str
    duration_ms: int
    trace_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    app.state.http = httpx.AsyncClient(
        base_url=os.environ["PAYMENT_SERVICE_URL"],
        timeout=httpx.Timeout(1.0),
    )
    app.state.producer = AIOKafkaProducer(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    )
    await app.state.producer.start()
    yield
    await app.state.producer.stop()
    await app.state.http.aclose()
    await app.state.pool.close()


app = FastAPI(title="order-service", lifespan=lifespan)
configure_tracing(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/orders", response_model=OrderResponse)
async def create_order(payload: OrderRequest) -> OrderResponse:
    started = time.perf_counter()
    order_id = uuid4()
    pool: asyncpg.Pool = app.state.pool

    await pool.execute(
        """
        INSERT INTO orders(id, customer_id, amount, status)
        VALUES($1, $2, $3, 'PENDING')
        """,
        order_id,
        payload.customer_id,
        payload.amount,
    )

    payment: dict | None = None
    last_payment_error: httpx.HTTPError | None = None
    for attempt_number in range(1, 3):
        await pool.execute(
            "INSERT INTO payment_attempts(order_id, attempt_number) VALUES($1, $2)",
            order_id,
            attempt_number,
        )
        try:
            payment_response = await app.state.http.post(
                "/payments",
                json={"order_id": str(order_id), "amount": str(payload.amount)},
            )
            payment_response.raise_for_status()
            payment = payment_response.json()
            break
        except httpx.HTTPError as error:
            last_payment_error = error

    if payment is None:
        await pool.execute(
            "UPDATE orders SET status='FAILED', updated_at=now() WHERE id=$1",
            order_id,
        )
        raise HTTPException(
            status_code=502, detail="Payment service failed"
        ) from last_payment_error

    await pool.execute(
        "UPDATE orders SET status='PAID', updated_at=now() WHERE id=$1",
        order_id,
    )

    event_id = uuid4()
    event = {
        "event_id": str(event_id),
        "event_type": "order.paid",
        "order_id": str(order_id),
        "payment_id": payment["payment_id"],
    }
    carrier: dict[str, str] = {}
    with tracer.start_as_current_span(
        "publish order.paid", kind=SpanKind.PRODUCER
    ):
        propagate.inject(carrier)
        headers = [(key, value.encode("utf-8")) for key, value in carrier.items()]
        await app.state.producer.send_and_wait(
            os.getenv("KAFKA_TOPIC", "order.paid"),
            json.dumps(event).encode("utf-8"),
            headers=headers,
        )

    await pool.execute(
        """
        INSERT INTO published_events(event_id, order_id, topic)
        VALUES($1, $2, $3)
        """,
        event_id,
        order_id,
        os.getenv("KAFKA_TOPIC", "order.paid"),
    )

    duration_ms = round((time.perf_counter() - started) * 1000)
    await pool.execute(
        """
        UPDATE orders
        SET request_duration_ms=$2, updated_at=now()
        WHERE id=$1
        """,
        order_id,
        duration_ms,
    )
    return OrderResponse(
        order_id=order_id,
        payment_id=UUID(payment["payment_id"]),
        status="PAID",
        duration_ms=duration_ms,
        trace_id=f"{trace.get_current_span().get_span_context().trace_id:032x}",
    )


@app.get("/orders/{order_id}/verification")
async def verify_order(order_id: UUID) -> dict:
    pool: asyncpg.Pool = app.state.pool
    row = await pool.fetchrow(
        """
        SELECT
            o.status,
            o.request_duration_ms,
            (SELECT count(*) FROM payments p WHERE p.order_id=o.id) AS payments,
            (SELECT count(*) FROM payment_attempts a WHERE a.order_id=o.id) AS attempts,
            (SELECT count(*) FROM published_events e WHERE e.order_id=o.id) AS events,
            (SELECT count(*) FROM notifications n WHERE n.order_id=o.id) AS notifications
        FROM orders o
        WHERE o.id=$1
        """,
        order_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")

    deadline_ms = int(os.getenv("REQUEST_DEADLINE_MS", "2000"))
    invariants = {
        "I1_single_payment": row["payments"] <= 1,
        "I2_paid_order_has_event": row["status"] != "PAID" or row["events"] == 1,
        "I3_request_within_deadline": (
            row["request_duration_ms"] is not None
            and row["request_duration_ms"] <= deadline_ms
        ),
        "I4_attempt_limit": row["attempts"] <= 3,
        "I5_single_notification": row["notifications"] <= 1,
    }
    return {
        "order_id": str(order_id),
        "status": row["status"],
        "counts": {
            "payments": row["payments"],
            "attempts": row["attempts"],
            "events": row["events"],
            "notifications": row["notifications"],
        },
        "duration_ms": row["request_duration_ms"],
        "invariants": invariants,
        "all_passed": all(invariants.values()),
    }

