"""Porta para execução e recuperação do fluxo de uma conversa."""

from typing import Any, Protocol

from app.agent_schemas.inputs import SessionContext


class ConversationFlow(Protocol):
    async def execute(
        self,
        message: str,
        *,
        user_id: str,
        farm_id: str,
        session_id: str,
        interaction_id: str | None = None,
        session_context: SessionContext | None = None,
        recipient_selectors: dict[str, dict[str, str]] | None = None,
        send_intent_requested: bool = False,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def resume(
        self,
        answer: str,
        *,
        question_id: str,
        session_id: str,
        interaction_id: str | None = None,
        session_context: SessionContext | None = None,
        recipient_selectors: dict[str, dict[str, str]] | None = None,
        send_intent_requested: bool = False,
        send_intent_source_interaction_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def read_state(self, session_id: str) -> dict[str, Any]: ...

    async def update_state(
        self,
        session_id: str,
        config: dict[str, Any],
        values: dict[str, Any],
        *,
        unavailable_message: str,
    ) -> None: ...
