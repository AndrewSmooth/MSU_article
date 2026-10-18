import asyncio
import os

import asyncpg


async def create_pool() -> asyncpg.Pool:
    database_url = os.environ["DATABASE_URL"]
    last_error: Exception | None = None

    for _ in range(30):
        try:
            return await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        except Exception as error:  # контейнер базы может ещё принимать подключения
            last_error = error
            await asyncio.sleep(1)

    raise RuntimeError("PostgreSQL is not available") from last_error

