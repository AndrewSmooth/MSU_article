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
from common.fault_injection import InjectedFault, FaultInjector, install_fault_routes
from common.observability import configure_tracing


tracer = trace.get_tracer(__name__)
fault_injector = FaultInjector(
    service_name="order-service",
    supported_points={
        "order.http.payment.create": {"delay", "error", "unavailable"},
        "order.kafka.order_paid.publish": {"delay", "error", "unavailable"},
        "order.db.order.read": {"delay", "error"},
    },
)


class OrderRequest(BaseModel):
    order_id: UUID | None = None
    customer_id: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, decimal_places=2)


class OrderResponse(BaseModel):
    order_id: UUID
    payment_id: UUID
    status: str
    duration_ms: int
    trace_id: str


def current_trace_id() -> str:
    return f"{trace.get_current_span().get_span_context().trace_id:032x}"


async def finish_request(
    pool: asyncpg.Pool, order_id: UUID, status: str, started: float
) -> int:
    duration_ms = round((time.perf_counter() - started) * 1000)
    await pool.execute(
        """
        UPDATE orders
        SET status=$2, request_duration_ms=$3, updated_at=now()
        WHERE id=$1
        """,
        order_id,
        status,
        duration_ms,
    )
    return duration_ms


def failure_detail(order_id: UUID, message: str) -> dict[str, str]:
    return {
        "message": message,
        "order_id": str(order_id),
        "trace_id": current_trace_id(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    app.state.http = httpx.AsyncClient(
        base_url=os.environ["PAYMENT_SERVICE_URL"],
        timeout=httpx.Timeout(float(os.getenv("PAYMENT_TIMEOUT_SECONDS", "1"))),
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
install_fault_routes(app, fault_injector)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/reset")
async def reset_scenario_state() -> dict[str, str]:
    pool: asyncpg.Pool = app.state.pool
    await pool.execute(
        """
        TRUNCATE TABLE
            notifications,
            published_events,
            payment_attempts,
            payments,
            orders
        RESTART IDENTITY
        """
    )
    return {"status": "reset"}


@app.post("/orders", response_model=OrderResponse)
async def create_order(payload: OrderRequest) -> OrderResponse:
    started = time.perf_counter()
    order_id = payload.order_id or uuid4()
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
    last_payment_error: Exception | None = None
    last_status_code: int | None = None
    for attempt_number in range(1, 3):
        await pool.execute(
            "INSERT INTO payment_attempts(order_id, attempt_number) VALUES($1, $2)",
            order_id,
            attempt_number,
        )
        try:
            async with fault_injector.point("order.http.payment.create"):
                payment_response = await app.state.http.post(
                    "/payments",
                    json={"order_id": str(order_id), "amount": str(payload.amount)},
                )
            payment_response.raise_for_status()
            payment = payment_response.json()
            break
        except httpx.HTTPStatusError as error:
            last_payment_error = error
            last_status_code = error.response.status_code
        except httpx.RequestError as error:
            last_payment_error = error
        except InjectedFault as error:
            last_payment_error = error
            last_status_code = error.activation.configuration.status_code

    if payment is None:
        permanent_error = (
            last_status_code is not None and 400 <= last_status_code < 500
        )
        failure_status = "FAILED_PERMANENT" if permanent_error else "FAILED"
        await finish_request(pool, order_id, failure_status, started)
        raise HTTPException(
            status_code=502,
            detail=failure_detail(order_id, "Payment service failed"),
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
    try:
        async with fault_injector.point("order.kafka.order_paid.publish"):
            with tracer.start_as_current_span(
                "publish order.paid", kind=SpanKind.PRODUCER
            ):
                propagate.inject(carrier)
                headers = [
                    (key, value.encode("utf-8")) for key, value in carrier.items()
                ]
                await app.state.producer.send_and_wait(
                    os.getenv("KAFKA_TOPIC", "order.paid"),
                    json.dumps(event).encode("utf-8"),
                    headers=headers,
                )
    except InjectedFault as error:
        await finish_request(pool, order_id, "PAID", started)
        raise HTTPException(
            status_code=error.activation.configuration.status_code,
            detail=failure_detail(order_id, "Event publication failed"),
        ) from error

    await pool.execute(
        """
        INSERT INTO published_events(event_id, order_id, topic)
        VALUES($1, $2, $3)
        """,
        event_id,
        order_id,
        os.getenv("KAFKA_TOPIC", "order.paid"),
    )

    duration_ms = await finish_request(pool, order_id, "PAID", started)
    return OrderResponse(
        order_id=order_id,
        payment_id=UUID(payment["payment_id"]),
        status="PAID",
        duration_ms=duration_ms,
        trace_id=current_trace_id(),
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
    retried_permanent_error = (
        row["status"] == "FAILED_PERMANENT" and row["attempts"] > 1
    )
    invariants = {
        "I1_single_payment": row["payments"] <= 1,
        "I2_paid_order_has_event": row["status"] != "PAID" or row["events"] == 1,
        "I3_request_within_deadline": (
            row["request_duration_ms"] is not None
            and row["request_duration_ms"] <= deadline_ms
        ),
        "I4_retry_policy": row["attempts"] <= 3 and not retried_permanent_error,
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
