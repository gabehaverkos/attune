"""The API process: every router, the artifact error mapping, and what runs beside them."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.chat.router import router as chat_router
from app.chat.artifacts import (
    ArtifactConflictError, ArtifactInterfaceDisabledError, ArtifactNotFoundError,
    ArtifactValidationError,
)
from app.chat.runs import RUN_EVENTS, RUN_QUEUED
from app.chat.worker import generation_loop
from app.demo.snapshot import router as demo_router
from app.interfaces.registry import INTERFACE_CATALOG, router as interfaces_router
from app.memory.public import router as memory_router
from app.core.queue import wakeups
from app.core.auth import guest_expiry_loop
from app.core.observability import configure_memory_logging, configure_tracing, shutdown_tracing
from app.shell import router as shell_router

configure_memory_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """What runs beside the routes: chat's runs, and expiring idle guests."""
    configure_tracing("attune-backend")
    wakeups.start(RUN_QUEUED, RUN_EVENTS)
    tasks = [asyncio.create_task(generation_loop()), asyncio.create_task(guest_expiry_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await wakeups.stop()
        await shutdown_tracing()


# Only the web app's server calls this API, through its /api proxy, so no browser
# origin needs CORS.
app = FastAPI(title="Attune", lifespan=lifespan)

app.include_router(shell_router)
for descriptor in INTERFACE_CATALOG:
    if descriptor.router:
        app.include_router(descriptor.router())
app.include_router(interfaces_router)
app.include_router(memory_router)
app.include_router(chat_router)
app.include_router(demo_router)


@app.exception_handler(ArtifactNotFoundError)
@app.exception_handler(ArtifactConflictError)
@app.exception_handler(ArtifactInterfaceDisabledError)
@app.exception_handler(ArtifactValidationError)
async def artifact_error_handler(request: Request, error: Exception) -> JSONResponse:
    statuses = {
        ArtifactNotFoundError: 404,
        ArtifactConflictError: 409,
        ArtifactInterfaceDisabledError: 403,
        ArtifactValidationError: 422,
    }
    detail: str | dict[str, object] = str(error)
    if isinstance(error, ArtifactValidationError):
        detail = {"message": str(error)}
        if error.missing_fields:
            detail["missing_fields"] = error.missing_fields
    status_code = next(status for error_type, status in statuses.items() if isinstance(error, error_type))
    return JSONResponse(status_code=status_code, content={"detail": detail})
