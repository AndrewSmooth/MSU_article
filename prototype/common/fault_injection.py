import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from pydantic import BaseModel, Field


FaultType = Literal["delay", "error", "unavailable", "lost_response"]


class FaultConfiguration(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=100)
    point_id: str = Field(min_length=1, max_length=150)
    fault_type: FaultType
    delay_ms: int = Field(default=0, ge=0, le=30000)
    status_code: int = Field(default=503, ge=400, le=599)
    activation_limit: int = Field(default=1, ge=1, le=100)


class FaultMode(BaseModel):
    enabled: bool


@dataclass(frozen=True)
class Activation:
    configuration: FaultConfiguration
    number: int


class InjectedFault(Exception):
    def __init__(self, activation: Activation):
        self.activation = activation
        super().__init__(
            f"Injected {activation.configuration.fault_type} at "
            f"{activation.configuration.point_id}"
        )


class FaultInjector:
    def __init__(self, service_name: str, supported_points: dict[str, set[str]]):
        self.service_name = service_name
        self.supported_points = supported_points
        self._configuration: FaultConfiguration | None = None
        self._activation_count = 0
        self._enabled = True
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
                "enabled": self._enabled,
                "armed": False,
                "activation_count": self._activation_count,
            }
        return {
            "service": self.service_name,
            "enabled": self._enabled,
            "armed": True,
            "configuration": self._configuration.model_dump(),
            "activation_count": self._activation_count,
            "activation_limit": self._configuration.activation_limit,
        }

    @asynccontextmanager
    async def point(self, point_id: str):
        if not self._enabled:
            yield
            return

        tracer = trace.get_tracer("fault-injection")
        with tracer.start_as_current_span(point_id) as span:
            span.set_attribute("fault.point.id", point_id)
            activation = await self._take_activation(point_id)
            if activation is None:
                span.set_attribute("fault.activated", False)
                yield
                return

            configuration = activation.configuration
            span.set_attribute("fault.activated", True)
            span.set_attribute("fault.experiment.id", configuration.experiment_id)
            span.set_attribute("fault.type", configuration.fault_type)
            span.set_attribute("fault.activation.number", activation.number)

            if configuration.fault_type == "delay":
                await asyncio.sleep(configuration.delay_ms / 1000)
                yield
                return

            if configuration.fault_type == "unavailable":
                await asyncio.sleep(configuration.delay_ms / 1000)
                raise InjectedFault(activation)

            if configuration.fault_type == "lost_response":
                await asyncio.sleep(configuration.delay_ms / 1000)
                raise InjectedFault(activation)

            if configuration.fault_type == "error":
                raise InjectedFault(activation)

            raise RuntimeError(f"Unsupported fault type: {configuration.fault_type}")

    async def _take_activation(self, point_id: str) -> Activation | None:
        async with self._lock:
            current = self._configuration
            if (
                current is None
                or current.point_id != point_id
                or self._activation_count >= current.activation_limit
            ):
                return None
            self._activation_count += 1
            return Activation(current, self._activation_count)

    async def set_enabled(self, enabled: bool) -> dict:
        async with self._lock:
            self._enabled = enabled
            if not enabled:
                self._configuration = None
                self._activation_count = 0
        return self.state()


def install_fault_routes(app: FastAPI, injector: FaultInjector) -> None:
    @app.exception_handler(InjectedFault)
    async def injected_fault_handler(
        request: Request, error: InjectedFault
    ) -> JSONResponse:
        configuration = error.activation.configuration
        return JSONResponse(
            status_code=configuration.status_code,
            content={
                "detail": "Injected fault",
                "experiment_id": configuration.experiment_id,
                "point_id": configuration.point_id,
                "fault_type": configuration.fault_type,
                "activation_number": error.activation.number,
            },
        )

    @app.put("/internal/fault")
    async def configure_fault(configuration: FaultConfiguration) -> dict:
        return await injector.configure(configuration)

    @app.get("/internal/fault")
    async def get_fault() -> dict:
        return injector.state()

    @app.delete("/internal/fault")
    async def clear_fault() -> dict:
        return await injector.clear()

    @app.put("/internal/fault/mode")
    async def set_fault_mode(mode: FaultMode) -> dict:
        return await injector.set_enabled(mode.enabled)
