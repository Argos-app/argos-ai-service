"""Interfaces e injeção dos serviços da aplicação."""

from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from app.application.contracts.interactions import InteractionCommand, InteractionOutcome
from app.application.contracts.messages import SessionMessagesPageView
from app.application.contracts.sessions import SessionView
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.application.identity import ActorContext
from app.application.message_history_service import MessageHistoryService
from app.application.session_service import SessionApplicationService
from app.infrastructure.observability.recording import active_collector


class SessionService(Protocol):
    async def create_session(self, principal: ActorContext) -> SessionView: ...

    async def list_sessions(self, principal: ActorContext) -> list[SessionView]: ...

    async def create_interaction(
        self, principal: ActorContext, session_id: UUID, body: InteractionCommand
    ) -> InteractionOutcome: ...


class MessageHistoryQueryService(Protocol):
    async def list_messages(
        self,
        principal: ActorContext,
        session_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> SessionMessagesPageView: ...


def _graph_provider(request: Request):
    app_state = request.app.state
    observation_collector = active_collector(getattr(app_state, "observability_collector", None))
    if app_state.agent_graph is not None:
        return app_state.agent_graph
    from app.agents.specialists import build_agent_factory
    from app.graph.workflow import build_graph, synthesis_timeout_with_runtime_grace

    models = getattr(app_state, "models", None)
    if models is None:
        raise HTTPException(
            status_code=503,
            detail=getattr(app_state, "model_initialization_error", None)
            or "Configuração dos modelos indisponível",
        )
    app_state.models = models
    try:
        persistence = getattr(app_state, "persistence", None)
        execution_events = persistence.execution_events if persistence is not None else None
        mcp_clients = getattr(app_state, "mcp_clients", None)
        email_dispatch = None
        if persistence is not None and mcp_clients is not None and mcp_clients.email is not None:
            email_dispatch = EmailDispatchWorkflow(
                email_client=mcp_clients.email,
                preparations=persistence.email_preparations,
                report_artifacts=persistence.report_artifacts,
                observation_collector=observation_collector,
            )
        graph = build_graph(
            build_agent_factory(
                models,
                execution_events=execution_events,
                mcp_clients=mcp_clients,
                documental_a2a_client=getattr(app_state, "documental_a2a_client", None),
                observation_collector=observation_collector,
                delegation_issuer=getattr(app_state, "mcp_delegation_token_issuer", None),
                report_artifacts=(
                    persistence.report_artifacts if persistence is not None else None
                ),
                email_preparations=(
                    persistence.email_preparations if persistence is not None else None
                ),
                report_signature_checker=getattr(
                    app_state, "report_source_signature_verifier", None
                ),
            ),
            checkpointer=app_state.checkpointer,
            report_artifacts=(persistence.report_artifacts if persistence is not None else None),
            email_preparations=(
                persistence.email_preparations if persistence is not None else None
            ),
            email_dispatch=email_dispatch,
            synthesis_agent_timeout_seconds=synthesis_timeout_with_runtime_grace(
                model_timeout_seconds=getattr(
                    getattr(app_state, "settings", None), "model_timeout_seconds", 60.0
                ),
                execution_timeout_seconds=getattr(
                    getattr(app_state, "settings", None), "execution_timeout_seconds", 180.0
                ),
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Configuração dos agentes indisponível",
        ) from exc
    app_state.agent_graph = graph
    return graph


def _summary_generator_provider(request: Request):
    app_state = request.app.state
    existing = getattr(app_state, "session_summary_generator", None)
    if existing is not None:
        return existing
    from app.agents.registry import ModelPolicyId

    models = getattr(app_state, "models", None)
    model = models.get(ModelPolicyId.ROUTER) if models is not None else None
    if model is None:
        return None
    from app.infrastructure.agents.session_summary_generator import (
        LangChainSessionSummaryGenerator,
    )
    from app.session_config import SESSION_SUMMARY_INPUT_MAX_TOKENS

    settings = getattr(app_state, "settings", None)
    generator = LangChainSessionSummaryGenerator(
        model,
        input_token_limit=(
            settings.session_summary_input_max_tokens
            if settings is not None
            else SESSION_SUMMARY_INPUT_MAX_TOKENS
        ),
        observation_collector=active_collector(getattr(app_state, "observability_collector", None)),
        langsmith_callback=(
            app_state.langsmith_tracing.callback
            if getattr(app_state, "langsmith_tracing", None) is not None
            else None
        ),
    )
    app_state.session_summary_generator = generator
    return generator


def _user_memory_extractor_provider(request: Request):
    app_state = request.app.state
    existing = getattr(app_state, "user_memory_extractor", None)
    if existing is not None:
        return existing
    from app.agents.registry import ModelPolicyId

    models = getattr(app_state, "models", None)
    model = models.get(ModelPolicyId.ROUTER) if models is not None else None
    if model is None:
        return None
    from app.infrastructure.agents.user_memory_extractor import LangChainUserMemoryExtractor

    extractor = LangChainUserMemoryExtractor(
        model,
        observation_collector=active_collector(getattr(app_state, "observability_collector", None)),
        langsmith_callback=(
            app_state.langsmith_tracing.callback
            if getattr(app_state, "langsmith_tracing", None) is not None
            else None
        ),
    )
    app_state.user_memory_extractor = extractor
    return extractor


def get_session_service(request: Request) -> SessionService:
    persistence = getattr(request.app.state, "persistence", None)
    if persistence is None:
        raise HTTPException(status_code=503, detail="Sessões indisponíveis")
    return SessionApplicationService(
        sessions=persistence.sessions,
        interactions=persistence.interactions,
        conversations=persistence.conversations,
        session_work=persistence.session_work,
        summaries=persistence.session_summaries,
        user_memory=persistence.user_memory,
        execution_events=persistence.execution_events,
        observation_collector=active_collector(
            getattr(request.app.state, "observability_collector", None)
        ),
        langsmith_callback=(
            request.app.state.langsmith_tracing.callback
            if getattr(request.app.state, "langsmith_tracing", None) is not None
            else None
        ),
        email_preparations=persistence.email_preparations,
        summary_generator_provider=lambda: _summary_generator_provider(request),
        user_memory_extractor_provider=lambda: _user_memory_extractor_provider(request),
        graph_provider=lambda: _graph_provider(request),
        execution_max_model_calls=getattr(
            getattr(request.app.state, "settings", None), "execution_max_model_calls", 12
        ),
        execution_max_tool_calls=getattr(
            getattr(request.app.state, "settings", None), "execution_max_tool_calls", 12
        ),
        execution_timeout_seconds=getattr(
            getattr(request.app.state, "settings", None), "execution_timeout_seconds", 90.0
        ),
    )


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]


def get_message_history_service(request: Request) -> MessageHistoryQueryService:
    persistence = getattr(request.app.state, "persistence", None)
    if persistence is None:
        raise HTTPException(status_code=503, detail="Sessões indisponíveis")
    return MessageHistoryService(
        sessions=persistence.sessions,
        conversations=persistence.conversations,
    )


MessageHistoryServiceDep = Annotated[
    MessageHistoryQueryService,
    Depends(get_message_history_service),
]
