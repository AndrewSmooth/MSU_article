import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from pydantic import BaseModel, Field


FaultType = Literal["lost_response"]


class FaultConfiguration(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=100)
    point_id: str = Field(min_length=1, max_length=150)
    fault_type: FaultType
    delay_ms: int = Field(default=1500, ge=1, le=10000)
    activation_limit: int = Field(default=1, ge=1, le=100)


class FaultInjector:
    def __init__(self, service_name: str, supported_points: dict[str, set[str]]):
        self.service_name = service_name
        self.supported_points = supported_points
        self._configuration: FaultConfiguration | None = None
        self._activation_count = 0
        self._lock = asyncio.Lock()

    async def configure(self, configuration: FaultConfiguration) -> dict:
        supported_faults = self.supported_points.get(configuration.point_id)
        if supported_faults is None:
            raise HTTPException(
                status_code=422,
                detail=f"Point {configuration.point_id!r} is not supported by {self.service_name}",
            )
        if configuration.fault_type not in supported_faults:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Fault {configuration.fault_type!r} is not supported at "
                    f"{configuration.point_id!r}"
                ),
            )

        async with self._lock:
            self._configuration = configuration
            self._activation_count = 0
        return self.state()

    async def clear(self) -> dict:
        async with self._lock:
            previous = self.state()
            self._configuration = None
            self._activation_count = 0
        return previous

    def state(self) -> dict:
        if self._configuration is None:
            return {
                "service": self.service_name,
                "armed": False,
                "activation_count": self._activation_count,
            }
        return {
            "service": self.service_name,
            "armed": True,
            "configuration": self._configuration.model_dump(),
            "activation_count": self._activation_count,
            "activation_limit": self._configuration.activation_limit,
        }

    @asynccontextmanager
    async def point(self, point_id: str):
        tracer = trace.get_tracer("fault-injection")
        with tracer.start_as_current_span(point_id) as span:
            span.set_attribute("fault.point.id", point_id)
            activated = await self._activate_if_armed(point_id)
            span.set_attribute("fault.activated", activated)
            yield

    async def _activate_if_armed(self, point_id: str) -> bool:
        configuration: FaultConfiguration | None = None
        activation_number = 0
        async with self._lock:
            current = self._configuration
            if (
                current is not None
                and current.point_id == point_id
                and self._activation_count < current.activation_limit
            ):
                self._activation_count += 1
                activation_number = self._activation_count
                configuration = current

        if configuration is None:
            return False

        span = trace.get_current_span()
        span.set_attribute("fault.experiment.id", configuration.experiment_id)
        span.set_attribute("fault.type", configuration.fault_type)
        span.set_attribute("fault.activation.number", activation_number)

        if configuration.fault_type == "lost_response":
            await asyncio.sleep(configuration.delay_ms / 1000)
            return True

        raise RuntimeError(f"Unsupported fault type: {configuration.fault_type}")


def install_fault_routes(app: FastAPI, injector: FaultInjector) -> None:
    @app.put("/internal/fault")
    async def configure_fault(configuration: FaultConfiguration) -> dict:
        return await injector.configure(configuration)

    @app.get("/internal/fault")
    async def get_fault() -> dict:
        return injector.state()

    @app.delete("/internal/fault")
    async def clear_fault() -> dict:
        return await injector.clear()
