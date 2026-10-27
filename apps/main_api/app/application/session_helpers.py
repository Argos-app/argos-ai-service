"""Helpers puros para validação e montagem de resultados de sessão."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.application.contracts.interactions import (
    AnswerCommand,
    AnswerResult,
    ChoiceAnswer,
    InteractionCommand,
    InteractionOutcome,
    MessageCommand,
    QuestionOptionResult,
    QuestionResult,
    TextAnswer,
)
from app.application.contracts.sessions import SessionView
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.domain.sessions import (
    ConversationMessage,
    InteractionKind,
    MessageKind,
    MessageRole,
    SessionRecord,
)
from app.privacy import minimize_pii, protect_user_input
from app.session_config import CLARIFICATION_TTL_SECONDS

SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _mongo_utcnow() -> datetime:
    """Use MongoDB's millisecond precision for timestamps returned and persisted together."""
    now = _utcnow()
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _authorized_farm_ids(
    session: SessionRecord, current_farms: Collection[UUID]
) -> tuple[UUID, ...]:
    current = set(current_farms)
    return tuple(farm_id for farm_id in session.farm_ids if farm_id in current)


def _has_full_farm_access(session: SessionRecord, current_farms: Collection[UUID]) -> bool:
    """Novas interações exigem acesso atual a todas as fazendas originais."""
    original = set(session.farm_ids)
    return bool(original) and original.issubset(set(current_farms))


def _session_response(
    session: SessionRecord, current_farms: Collection[UUID]
) -> SessionView | None:
    farm_ids = _authorized_farm_ids(session, current_farms)
    if not farm_ids:
        return None
    return SessionView(
        session_id=session.session_id,
        farm_ids=farm_ids,
        created_at=session.created_at,
    )


def _normalize_input(
    body: InteractionCommand,
) -> tuple[
    InteractionKind,
    str,
    str,
    str,
    str,
    UUID,
    dict[str, dict[str, str]],
    bool,
]:
    recipient_selectors: dict[str, dict[str, str]] = {}
    send_intent_requested = False
    if isinstance(body, MessageCommand):
        protected = protect_user_input(body.message)
        sanitized = protected.sanitized_text
        recipient_selectors = protected.recipient_selectors
        send_intent_requested = protected.send_intent_requested
        if not sanitized.strip():
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT, "A mensagem não pode estar vazia"
            )
        payload = {"kind": "message", "message": sanitized}
        key = str(body.client_message_id)
        public_id = body.client_message_id
        kind = InteractionKind.MESSAGE
        content = sanitized
    else:
        answer = body.answer.model_dump(mode="json")
        if isinstance(body.answer, TextAnswer):
            protected = protect_user_input(body.answer.text)
            answer["text"] = protected.sanitized_text
            recipient_selectors = protected.recipient_selectors
            content = answer["text"]
        elif isinstance(body.answer, ChoiceAnswer):
            content = f"Opção selecionada: {body.answer.option_id}"
        else:
            content = "Sim" if body.answer.confirm else "Não"
        if not content.strip():
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT, "A resposta não pode estar vazia"
            )
        payload = {
            "kind": "answer",
            "interaction_id": str(body.interaction_id),
            "question_id": str(body.question_id),
            "answer": answer,
        }
        key = str(body.question_id)
        public_id = body.question_id
        kind = InteractionKind.ANSWER
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    protected_digest = hashlib.sha256(
        json.dumps(recipient_selectors, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    hash_payload = f"{payload_json}:{protected_digest}:{send_intent_requested}"
    digest = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
    return (
        kind,
        key,
        content,
        payload_json,
        digest,
        public_id,
        recipient_selectors,
        send_intent_requested,
    )


def _memory_input(
    body: InteractionCommand,
    sanitized_input: str,
    answer_text: str | None,
) -> str | None:
    if isinstance(body, MessageCommand):
        return sanitized_input
    if isinstance(body, AnswerCommand) and isinstance(body.answer, TextAnswer):
        return answer_text or sanitized_input
    return None


def _interaction_id(
    session_id: UUID,
    kind: InteractionKind,
    key: str,
    input_hash: str,
) -> UUID:
    return uuid5(NAMESPACE_URL, f"argos:{session_id}:{kind.value}:{key}:{input_hash}")


def _response_from_state(
    *,
    session_id: UUID,
    interaction_id: UUID,
    public_request_id: UUID,
    state: dict[str, Any],
    now: datetime,
) -> InteractionOutcome:
    request = state.get("clarification_request")
    prompt = state.get("clarification_question")
    question_id = state.get("question_id")
    if request is not None and prompt and question_id:
        expires_at = now + timedelta(seconds=CLARIFICATION_TTL_SECONDS)
        pending_expiry = state.get("clarification_expires_at")
        if pending_expiry:
            try:
                expires_at = min(expires_at, datetime.fromisoformat(pending_expiry))
            except TypeError, ValueError:
                expires_at = now
        if expires_at <= now:
            response = AnswerResult(
                kind="answer",
                text="A pergunta expirou. Inicie uma nova solicitação para continuar.",
            )
        else:
            response = QuestionResult(
                kind="question",
                question_id=UUID(str(question_id)),
                input_type=request.input_type,
                prompt=minimize_pii(_hide_report_artifact_refs(prompt, state)),
                reason_code=request.reason_code,
                options=[
                    QuestionOptionResult.model_validate(item.model_dump())
                    for item in request.options
                ],
                expires_at=expires_at,
            )
    else:
        response = AnswerResult(
            kind="answer",
            text=minimize_pii(
                _hide_report_artifact_refs(state.get("final_answer") or SAFE_ANSWER, state)
            ),
        )
    return InteractionOutcome(
        status="degraded" if state.get("errors") else "completed",
        degradation_reasons=tuple(
            dict.fromkeys(
                error
                for error in state.get("errors", [])
                if isinstance(error, str) and error.isascii() and error.replace("_", "").isalnum()
            )
        ),
        session_id=session_id,
        interaction_id=interaction_id,
        public_request_id=public_request_id,
        response=response,
    )


def _hide_report_artifact_refs(text: str, state: dict[str, Any]) -> str:
    """Remove executor-only artifact references from text returned to the user."""
    references_by_task = state.get("report_artifact_refs")
    if not isinstance(references_by_task, dict):
        return text
    references: set[str] = set()
    for value in references_by_task.values():
        if isinstance(value, str):
            references.add(value)
        elif isinstance(value, (tuple, list, set)):
            references.update(item for item in value if isinstance(item, str))
    for reference in references:
        if reference:
            text = re.sub(re.escape(reference), "[referência interna]", text, flags=re.IGNORECASE)
    return text


def _assistant_message(
    *,
    session_id: UUID,
    interaction_id: UUID,
    response: InteractionOutcome,
    created_at: datetime,
) -> ConversationMessage:
    is_question = isinstance(response.response, QuestionResult)
    content = response.response.prompt if is_question else response.response.text
    return ConversationMessage(
        message_id=uuid5(interaction_id, "assistant"),
        session_id=session_id,
        interaction_id=interaction_id,
        sequence=1,
        role=MessageRole.ASSISTANT,
        kind=MessageKind.CLARIFICATION_QUESTION if is_question else MessageKind.ASSISTANT_ANSWER,
        sanitized_content=minimize_pii(content),
        created_at=created_at,
        payload_json=response.model_dump_json(),
    )
