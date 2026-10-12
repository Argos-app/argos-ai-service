"""Ponto de entrada HTTP do serviço principal."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.infrastructure.bootstrap import ServiceRuntime, create_runtime
from app.infrastructure.mcp import (
    McpDelegationTokenIssuer,
    bind_observation_collector,
    create_mcp_clients,
)
from app.infrastructure.observability.recording import OperationRecorder, active_collector
from app.settings import Settings
from packages.observability import (
    DisabledObservationCollector,
    ErrorOrigin,
    HttpMetadata,
    ObservationStatus,
    OperationKind,
    OperationPhase,
)


def create_app(
    settings: Settings | None = None,
    *,
    agent_graph: Any = None,
    models: dict[Any, Any] | None = None,
    checkpointer: Any = None,
    session_summary_generator: Any = None,
    user_memory_extractor: Any = None,
) -> FastAPI:
    configured = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime: ServiceRuntime | None = None
        if configured.mongo_uri:
            runtime = await create_runtime(configured)
        app.state.persistence = runtime.persistence if runtime else None
        app.state.mongo_database = runtime.database if runtime else None
        app.state.observability_collector = (
            getattr(runtime, "observation_collector", DisabledObservationCollector())
            if runtime
            else DisabledObservationCollector()
        )
        app.state.mcp_clients = bind_observation_collector(
            app.state.mcp_clients, app.state.observability_collector
        )
        app.state.checkpointer = (
            checkpointer
            if checkpointer is not None
            else (runtime.checkpointer if runtime else None)
        )
        try:
            yield
        finally:
            if runtime:
                await runtime.close()

    app = FastAPI(title="Argos AI Service", version="0.1.0", lifespan=lifespan)
    app.state.agent_graph = agent_graph
    app.state.models = models
    app.state.checkpointer = checkpointer
    app.state.session_summary_generator = session_summary_generator
    app.state.user_memory_extractor = user_memory_extractor
    app.state.settings = configured
    app.state.observability_collector = DisabledObservationCollector()
    app.state.mcp_clients = create_mcp_clients(configured)
    app.state.mcp_delegation_token_issuer = (
        McpDelegationTokenIssuer(configured)
        if configured.mcp_delegation_key_id and configured.mcp_delegation_private_key
        else None
    )

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > 32 * 1024:
                return JSONResponse(status_code=413, content={"detail": "Corpo excede 32 KiB"})
        return await call_next(request)

    @app.middleware("http")
    async def observe_http(request: Request, call_next):
        collector = active_collector(app.state.observability_collector)
        if collector is None or request.url.path in {"/health/live", "/health/ready"}:
            return await call_next(request)
        recorder = OperationRecorder(
            collector,
            operation_kind=OperationKind.HTTP,
            operation_name="http.request",
            operation_phase=OperationPhase.EXECUTION,
            error_origin=ErrorOrigin.APPLICATION,
        )
        try:
            response = await call_next(request)
        except BaseException as exc:
            recorder.finish(error=exc)
            raise
        route = request.scope.get("route")
        route_template = getattr(route, "path", "unmatched")
        status_code = response.status_code
        recorder.finish(
            status=(
                ObservationStatus.FAILED
                if status_code >= 500
                else ObservationStatus.REJECTED
                if status_code >= 400
                else ObservationStatus.SUCCEEDED
            ),
            http=HttpMetadata(
                method=request.method[:12],
                route=route_template[:200],
                status_code=status_code,
            ),
        )
        return response

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
        if not configured.auth_trusted_public_keys:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": "identity_not_configured"},
            )
        if getattr(app.state, "checkpointer", None) is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": "graph_runtime_not_configured"},
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
