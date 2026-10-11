"""Ponto de entrada HTTP do serviço principal."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.infrastructure.bootstrap import ServiceRuntime, create_runtime
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime: ServiceRuntime | None = None
        if configured.mongo_uri:
            runtime = await create_runtime(configured)
        app.state.persistence = runtime.persistence if runtime else None
        app.state.mongo_database = runtime.database if runtime else None
        try:
            yield
        finally:
            if runtime:
                await runtime.close()

    app = FastAPI(title="Argos AI Service", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > 32 * 1024:
                return JSONResponse(status_code=413, content={"detail": "Corpo excede 32 KiB"})
        return await call_next(request)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["health"],
        response_model=None,
        responses={503: {"description": "Persistência Mongo indisponível"}},
    )
    async def ready():
        if getattr(app.state, "persistence", None) is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": "session_store_not_configured"},
            )
        try:
            await app.state.mongo_database.command("ping")
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": "session_store_unavailable"},
            )
        return {"status": "ok"}

    app.include_router(v1_router)
    return app


app = create_app()
