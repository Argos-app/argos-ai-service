"""Atualização concorrente da memória permitida do usuário."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.domain.memory import (
    MemoryFieldAction,
    UserMemory,
    UserMemoryChange,
    UserMemoryFieldChange,
)
from app.ports.memory_generators import UserMemoryExtractor
from app.ports.user_memory_repository import UserMemoryRepository
from app.privacy import minimize_sensitive_memory_input

logger = logging.getLogger(__name__)
ExtractorProvider = Callable[[], UserMemoryExtractor | None]
MAX_CAS_ATTEMPTS = 3

_DELETE_COMMAND = re.compile(
    r"\b(?:esqueca|esquecer|apague|apagar|remova|remover|exclua|excluir|"
    r"delete|deletar|retire|retirar|"
    r"pare de (?:usar|considerar)|deixe de (?:usar|considerar)|"
    r"nao (?:considere mais|use mais|leve mais em conta|quero mais))\b"
)
_NEGATED_DELETE = re.compile(r"\b(?:nao|nunca)\b.{0,32}\b(?:quero|peco|desejo)\b")
_NEGATED_IMPERATIVE = re.compile(r"\b(?:nao|nunca)(?:\s+\w+){0,4}\s*$")
_TONE_MARKERS = re.compile(
    r"\b(?:tom|estilo|linguagem|forma de falar|jeito de falar|respostas?|explicacoes?)\b"
)
_TONE_DECLARATION = re.compile(r"\b(?:prefiro|gosto|quero|peco|sempre|use|responda)\b")
_GOAL_MARKERS = re.compile(r"\b(?:objetivo|meta|proposito|finalidade)\b")
_PERSONAL_GOAL = re.compile(r"\b(?:meu|minha)\s+(?:objetivo|meta|proposito|finalidade)\b")
_ALL_MEMORY_MARKERS = re.compile(r"\b(?:memoria|perfil|preferencias)\b")


class UserMemoryService:
    def __init__(
        self,
        *,
        repository: UserMemoryRepository,
        extractor_provider: ExtractorProvider | None,
    ) -> None:
        self._repository = repository
        self._extractor_provider = extractor_provider

    async def update_from_user_entry(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        source_created_at: datetime,
        sanitized_user_input: str,
    ) -> UserMemory | None:
        safe_input = minimize_sensitive_memory_input(sanitized_user_input)
        current = await self._repository.get(user_id)
        explicit_deletions = _explicit_deletions(safe_input)
        change = UserMemoryChange()
        if self._extractor_provider is not None and _should_extract_memory(
            safe_input,
            explicit_deletions=explicit_deletions,
        ):
            try:
                extractor = self._extractor_provider()
                if extractor is not None:
                    change = await extractor.extract(
                        user_id=user_id,
                        session_id=session_id,
                        sanitized_user_input=safe_input,
                        current=current,
                    )
            except Exception:
                logger.exception(
                    "falha ao extrair memória do usuário %s; somente pedidos explícitos de"
                    " remoção serão considerados",
                    user_id,
                )
        change = _apply_explicit_deletions(change, explicit_deletions)
        change = _validate_changes(change, safe_input)

        for _ in range(MAX_CAS_ATTEMPTS):
            candidate = _apply_change(
                current,
                change,
                user_id=user_id,
                session_id=session_id,
                interaction_id=interaction_id,
                source_created_at=source_created_at,
            )
            if candidate is None:
                return current
            if await self._repository.save_if_current(
                candidate,
                expected_revision=current.revision if current else None,
            ):
                return candidate
            current = await self._repository.get(user_id)

        logger.warning("conflito de revisão ao atualizar memória do usuário %s", user_id)
        return current


def _explicit_deletions(text: str) -> UserMemoryChange:
    normalized = _normalize(text)
    commands = list(_DELETE_COMMAND.finditer(normalized))
    if not any(_is_positive_delete_command(normalized, match) for match in commands):
        return UserMemoryChange()

    tone_marker = bool(_TONE_MARKERS.search(normalized))
    goal_marker = bool(_GOAL_MARKERS.search(normalized))
    deletes_all = bool(_ALL_MEMORY_MARKERS.search(normalized)) and not (tone_marker or goal_marker)
    has_tone = deletes_all or tone_marker
    has_goal = deletes_all or goal_marker
    return UserMemoryChange(
        tone=(
            UserMemoryFieldChange(action=MemoryFieldAction.DELETE)
            if has_tone
            else UserMemoryFieldChange()
        ),
        general_goal=(
            UserMemoryFieldChange(action=MemoryFieldAction.DELETE)
            if has_goal
            else UserMemoryFieldChange()
        ),
    )


def _should_extract_memory(text: str, *, explicit_deletions: UserMemoryChange) -> bool:
    """Só chama o modelo quando a mensagem pode alterar memória duradoura."""
    normalized = _normalize(text)
    has_positive_tone = bool(
        _TONE_MARKERS.search(normalized) and _TONE_DECLARATION.search(normalized)
    )
    has_positive_goal = bool(_PERSONAL_GOAL.search(normalized))
    has_positive_memory = bool(
        _ALL_MEMORY_MARKERS.search(normalized)
        and re.search(r"\b(?:prefiro|quero|registre|lembre|considere)\b", normalized)
    )
    if has_positive_tone or has_positive_goal or has_positive_memory:
        return True
    return (
        explicit_deletions.tone.action != MemoryFieldAction.DELETE
        and explicit_deletions.general_goal.action != MemoryFieldAction.DELETE
        and bool(_GOAL_MARKERS.search(normalized) or _TONE_MARKERS.search(normalized))
    )


def _is_positive_delete_command(text: str, match: re.Match[str]) -> bool:
    command = match.group(0)
    prefix = text[max(0, match.start() - 48) : match.start()]
    if command.startswith("nao "):
        return True
    return not (_NEGATED_DELETE.search(prefix) or _NEGATED_IMPERATIVE.search(prefix))


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _apply_explicit_deletions(
    extracted: UserMemoryChange,
    deletions: UserMemoryChange,
) -> UserMemoryChange:
    return UserMemoryChange(
        tone=(
            deletions.tone
            if deletions.tone.action == MemoryFieldAction.DELETE
            else _reject_unconfirmed_delete(extracted.tone)
        ),
        general_goal=(
            deletions.general_goal
            if deletions.general_goal.action == MemoryFieldAction.DELETE
            else _reject_unconfirmed_delete(extracted.general_goal)
        ),
    )


def _reject_unconfirmed_delete(change: UserMemoryFieldChange) -> UserMemoryFieldChange:
    return UserMemoryFieldChange() if change.action == MemoryFieldAction.DELETE else change


def _validate_changes(change: UserMemoryChange, source: str) -> UserMemoryChange:
    return UserMemoryChange(
        tone=_validate_field_change(change.tone, source, max_value_length=256),
        general_goal=_validate_field_change(change.general_goal, source, max_value_length=1000),
    )


def _validate_field_change(
    change: UserMemoryFieldChange,
    source: str,
    *,
    max_value_length: int,
) -> UserMemoryFieldChange:
    if change.action != MemoryFieldAction.SET:
        return change
    value = minimize_sensitive_memory_input(change.value or "").strip()
    excerpt = change.source_excerpt or ""
    if not value or len(value) > max_value_length or len(excerpt) > 512 or excerpt not in source:
        return UserMemoryFieldChange()
    return UserMemoryFieldChange(
        action=MemoryFieldAction.SET,
        value=value,
        source_excerpt=minimize_sensitive_memory_input(excerpt).strip(),
    )


def _apply_change(
    current: UserMemory | None,
    change: UserMemoryChange,
    *,
    user_id: UUID,
    session_id: UUID,
    interaction_id: UUID,
    source_created_at: datetime,
) -> UserMemory | None:
    tone = current.tone if current else None
    general_goal = current.general_goal if current else None
    tone_session_id = current.tone_source_session_id if current else None
    tone_interaction_id = current.tone_source_interaction_id if current else None
    goal_session_id = current.goal_source_session_id if current else None
    goal_interaction_id = current.goal_source_interaction_id if current else None
    tone_excerpt = current.tone_source_excerpt if current else None
    goal_excerpt = current.goal_source_excerpt if current else None
    tone_updated_at = current.tone_updated_at if current else None
    goal_updated_at = current.goal_updated_at if current else None
    changed = False

    if current is not None:
        tone_updated_at = tone_updated_at or (current.updated_at if tone is not None else None)
        goal_updated_at = goal_updated_at or (
            current.updated_at if general_goal is not None else None
        )

    if change.tone.action != MemoryFieldAction.UNCHANGED and (
        tone_updated_at is None or source_created_at > tone_updated_at
    ):
        tone, tone_session_id, tone_interaction_id, tone_excerpt = _updated_field(
            change.tone, session_id, interaction_id, tone
        )
        tone_updated_at = source_created_at
        changed = True

    if change.general_goal.action != MemoryFieldAction.UNCHANGED and (
        goal_updated_at is None or source_created_at > goal_updated_at
    ):
        general_goal, goal_session_id, goal_interaction_id, goal_excerpt = _updated_field(
            change.general_goal, session_id, interaction_id, general_goal
        )
        goal_updated_at = source_created_at
        changed = True

    if not changed:
        return None

    return UserMemory(
        user_id=user_id,
        tone=tone,
        general_goal=general_goal,
        tone_source_session_id=tone_session_id,
        tone_source_interaction_id=tone_interaction_id,
        goal_source_session_id=goal_session_id,
        goal_source_interaction_id=goal_interaction_id,
        revision=(current.revision + 1) if current else 1,
        updated_at=datetime.now(UTC),
        tone_source_excerpt=tone_excerpt,
        goal_source_excerpt=goal_excerpt,
        tone_updated_at=tone_updated_at,
        goal_updated_at=goal_updated_at,
    )


def _updated_field(
    change: UserMemoryFieldChange,
    session_id: UUID,
    interaction_id: UUID,
    current_value: str | None,
) -> tuple[str | None, UUID | None, UUID | None, str | None]:
    if change.action == MemoryFieldAction.DELETE:
        return None, None, None, None
    if change.action != MemoryFieldAction.SET:
        return current_value, None, None, None
    value = minimize_sensitive_memory_input(change.value or "").strip()
    excerpt = minimize_sensitive_memory_input(change.source_excerpt or "").strip()
    if not value or not excerpt:
        return current_value, None, None, None
    return value, session_id, interaction_id, excerpt
