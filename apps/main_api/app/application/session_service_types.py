"""Types internos compartilhados pelo serviço de sessões."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.agent_schemas.inputs import SessionContext
from app.application.contracts.interactions import InteractionCommand
from app.application.identity import ActorContext
from app.domain.sessions import (
    ConversationMessage,
    InteractionRecord,
    ProcessingLease,
    SessionRecord,
)
from app.ports.conversation_flow import ConversationFlow

EMAIL_SEND_CONFIRMATION_REASON = "EMAIL_SEND_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class _InteractionRequest:
    body: InteractionCommand
    authorized_farms: tuple[UUID, ...]
    sanitized_input: str
    public_request_id: UUID
    recipient_selectors: dict[str, dict[str, str]]
    send_intent_requested: bool
    answer_text: str | None
    send_intent_source_interaction_id: str | None
    send_confirmation: bool = False
    confirmed_send_question_id: UUID | None = None
    confirmed_send_report_id: UUID | None = None
    confirmed_send_question_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _AcceptedInteraction:
    principal: ActorContext
    session: SessionRecord
    request: _InteractionRequest
    graph: ConversationFlow
    accepted_message: ConversationMessage
    lease: ProcessingLease
    lease_lost: asyncio.Event
    running: InteractionRecord


@dataclass(frozen=True, slots=True)
class _InteractionContext:
    session_context: SessionContext
    graph_config: dict[str, Any]
