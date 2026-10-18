import asyncio
import json
import os
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

from common.database import create_pool
from common.observability import configure_tracing


tracer = trace.get_tracer(__name__)


async def consume_events(app: FastAPI) -> None:
    consumer: AIOKafkaConsumer = app.state.consumer
    pool: asyncpg.Pool = app.state.pool

    async for message in consumer:
        headers = {
            key: value.decode("utf-8")
            for key, value in (message.headers or [])
            if value is not None
        }
        parent_context = propagate.extract(headers)
        with tracer.start_as_current_span(
            "consume order.paid",
            context=parent_context,
            kind=SpanKind.CONSUMER,
        ):
            event = json.loads(message.value.decode("utf-8"))
            await pool.execute(
                """
                INSERT INTO notifications(event_id, order_id)
                VALUES($1, $2)
                ON CONFLICT (event_id) DO NOTHING
                """,
                UUID(event["event_id"]),
                UUID(event["order_id"]),
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    app.state.consumer = AIOKafkaConsumer(
        os.getenv("KAFKA_TOPIC", "order.paid"),
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        group_id=os.getenv("KAFKA_GROUP_ID", "notification-service"),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await app.state.consumer.start()
    task = asyncio.create_task(consume_events(app))
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await app.state.consumer.stop()
    await app.state.pool.close()


app = FastAPI(title="notification-service", lifespan=lifespan)
configure_tracing(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

