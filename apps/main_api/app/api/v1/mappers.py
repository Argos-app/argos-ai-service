"""Conversões explícitas entre DTOs HTTP e contratos dos casos de uso."""

from typing import TypeVar

from pydantic import TypeAdapter

from app.api.v1.dependencies.auth import Principal, to_actor_context
from app.api.v1.models.interactions import InteractionInput, InteractionResult
from app.api.v1.models.messages import SessionMessagesPage
from app.api.v1.models.sessions import Session
from app.application.contracts.interactions import InteractionCommand, InteractionOutcome
from app.application.contracts.messages import SessionMessagesPageView
from app.application.contracts.sessions import SessionView
from app.application.identity import ActorContext

ModelT = TypeVar("ModelT")
_INTERACTION_COMMAND_ADAPTER = TypeAdapter(InteractionCommand)


def actor_from_http(principal: Principal) -> ActorContext:
    return to_actor_context(principal)


def interaction_command_from_http(body: InteractionInput) -> InteractionCommand:
    return _INTERACTION_COMMAND_ADAPTER.validate_python(body.model_dump(mode="python"))


def interaction_result_to_http(result: InteractionOutcome) -> InteractionResult:
    payload = result.model_dump(mode="python")
    payload["created_at"] = result.created_at
    return InteractionResult.model_validate(payload)


def session_to_http(session: SessionView) -> Session:
    return Session.model_validate(session.model_dump(mode="python"))


def sessions_to_http(sessions: list[SessionView]) -> list[Session]:
    return [session_to_http(session) for session in sessions]


def message_page_to_http(page: SessionMessagesPageView) -> SessionMessagesPage:
    return SessionMessagesPage.model_validate(page.model_dump(mode="python"))
