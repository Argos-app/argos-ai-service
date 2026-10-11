"""Interfaces e injeção dos serviços da aplicação."""

from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from app.api.v1.dependencies.auth import Principal
from app.api.v1.models.interactions import InteractionInput, InteractionResult
from app.api.v1.models.sessions import Session
from app.application.session_service import MongoSessionService


class SessionService(Protocol):
    async def create_session(self, principal: Principal) -> Session: ...

    async def list_sessions(self, principal: Principal) -> list[Session]: ...

    async def create_interaction(
        self, principal: Principal, session_id: UUID, body: InteractionInput
    ) -> InteractionResult: ...


def _graph_provider(request: Request):
    app_state = request.app.state
    if app_state.agent_graph is not None:
        return app_state.agent_graph
    from app.agents.registry import ModelPolicyId
    from app.agents.specialists import build_agent_factory
    from app.graph.workflow import build_graph

    models = getattr(app_state, "models", None)
    if models is None:
        from app.llms import llm_especialista, llm_rapido

        if llm_rapido is None or llm_especialista is None:
            return None
        models = {
            ModelPolicyId.ROUTER: llm_rapido,
            ModelPolicyId.FARM: llm_especialista,
            ModelPolicyId.DOCTOR: llm_especialista,
            ModelPolicyId.ANALYST: llm_especialista,
            ModelPolicyId.SECRETARY: llm_especialista,
            ModelPolicyId.INFORMATION: llm_especialista,
            ModelPolicyId.ORCHESTRATOR: llm_rapido,
            ModelPolicyId.JUDGE: llm_rapido,
            ModelPolicyId.QUESTION_FORMATTER: llm_rapido,
        }
    app_state.models = models
    try:
        persistence = getattr(app_state, "persistence", None)
        execution_events = persistence.execution_events if persistence is not None else None
        graph = build_graph(
            build_agent_factory(models, execution_events=execution_events),
            checkpointer=app_state.checkpointer,
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
        from app.llms import llm_rapido

        model = llm_rapido
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
        from app.llms import llm_rapido

        model = llm_rapido
    if model is None:
        return None
    from app.infrastructure.agents.user_memory_extractor import LangChainUserMemoryExtractor

    extractor = LangChainUserMemoryExtractor(model)
    app_state.user_memory_extractor = extractor
    return extractor


def get_session_service(request: Request) -> SessionService:
    persistence = getattr(request.app.state, "persistence", None)
    if persistence is None:
        raise HTTPException(status_code=503, detail="Sessões indisponíveis")
    return MongoSessionService(
        sessions=persistence.sessions,
        interactions=persistence.interactions,
        conversations=persistence.conversations,
        session_work=persistence.session_work,
        summaries=persistence.session_summaries,
        user_memory=persistence.user_memory,
        execution_events=persistence.execution_events,
        summary_generator_provider=lambda: _summary_generator_provider(request),
        user_memory_extractor_provider=lambda: _user_memory_extractor_provider(request),
        graph_provider=lambda: _graph_provider(request),
    )


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
