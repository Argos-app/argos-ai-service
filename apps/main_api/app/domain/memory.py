"""Modelos para resumos de sessão e perfil persistente do usuário."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.common import require_utc
from app.session_config import SESSION_SUMMARY_MAX_TOKENS


@dataclass(frozen=True, slots=True)
class SummaryFact:
    """Dado importante do resumo com proveniência opcional."""

    content: str
    source_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("content do fato não pode estar vazio")


class MemoryFieldAction(StrEnum):
    UNCHANGED = "unchanged"
    SET = "set"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class UserMemoryFieldChange:
    action: MemoryFieldAction = MemoryFieldAction.UNCHANGED
    value: str | None = None
    source_excerpt: str | None = None

    def __post_init__(self) -> None:
        if self.action == MemoryFieldAction.SET:
            if not self.value or not self.value.strip():
                raise ValueError("set exige valor de memória não vazio")
            if not self.source_excerpt or not self.source_excerpt.strip():
                raise ValueError("set exige trecho de origem não vazio")
        elif self.value is not None or self.source_excerpt is not None:
            raise ValueError("valor e trecho só são válidos para ação set")


@dataclass(frozen=True, slots=True)
class UserMemoryChange:
    tone: UserMemoryFieldChange = UserMemoryFieldChange()
    general_goal: UserMemoryFieldChange = UserMemoryFieldChange()

    def __post_init__(self) -> None:
        if not isinstance(self.tone, UserMemoryFieldChange):
            raise TypeError("tone deve ser UserMemoryFieldChange")
        if not isinstance(self.general_goal, UserMemoryFieldChange):
            raise TypeError("general_goal deve ser UserMemoryFieldChange")


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Resumo incremental; sua revisão cobre uma faixa explícita de mensagens."""

    session_id: UUID
    revision: int
    through_sequence: int
    through_user_message_number: int
    activity: str
    important_facts: tuple[SummaryFact, ...]
    decisions: tuple[str, ...]
    pending_activity: str | None
    limitations: tuple[str, ...]
    summary_text: str
    token_count: int
    updated_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.updated_at, "updated_at")
        if self.revision < 1 or self.through_sequence < 1:
            raise ValueError("revision e through_sequence devem ser positivos")
        if self.through_user_message_number < 1:
            raise ValueError("resumo exige ao menos uma entrada do usuário")
        if not self.activity.strip() or not self.summary_text.strip():
            raise ValueError("activity e summary_text não podem estar vazios")
        if not 1 <= self.token_count <= SESSION_SUMMARY_MAX_TOKENS:
            raise ValueError(f"token_count deve estar entre 1 e {SESSION_SUMMARY_MAX_TOKENS}")
        if any(not decision.strip() for decision in self.decisions):
            raise ValueError("decisions não pode conter valores vazios")
        if any(not limitation.strip() for limitation in self.limitations):
            raise ValueError("limitations não pode conter valores vazios")


@dataclass(frozen=True, slots=True)
class UserMemory:
    """Perfil independente da sessão, restrito ao tom e objetivo geral."""

    user_id: UUID
    tone: str | None
    general_goal: str | None
    tone_source_session_id: UUID | None
    tone_source_interaction_id: UUID | None
    goal_source_session_id: UUID | None
    goal_source_interaction_id: UUID | None
    revision: int
    updated_at: datetime
    tone_source_excerpt: str | None = None
    goal_source_excerpt: str | None = None
    tone_updated_at: datetime | None = None
    goal_updated_at: datetime | None = None

    def __post_init__(self) -> None:
        require_utc(self.updated_at, "updated_at")
        if self.tone_updated_at is not None:
            require_utc(self.tone_updated_at, "tone_updated_at")
        if self.goal_updated_at is not None:
            require_utc(self.goal_updated_at, "goal_updated_at")
        if self.revision < 1:
            raise ValueError("revision deve ser positiva")
        if self.tone is not None and not self.tone.strip():
            raise ValueError("tone deve ser omitido, não vazio")
        if self.tone is not None and len(self.tone) > 256:
            raise ValueError("tone não pode exceder 256 caracteres")
        if self.general_goal is not None and not self.general_goal.strip():
            raise ValueError("general_goal deve ser omitido, não vazio")
        if self.general_goal is not None and len(self.general_goal) > 1000:
            raise ValueError("general_goal não pode exceder 1000 caracteres")
        if (self.tone is None) != (self.tone_source_session_id is None):
            raise ValueError("origem de tone deve acompanhar seu valor")
        if (self.tone is None) != (self.tone_source_interaction_id is None):
            raise ValueError("interação de origem de tone deve acompanhar seu valor")
        if self.tone is None and self.tone_source_excerpt is not None:
            raise ValueError("trecho de origem de tone exige valor")
        if self.tone_source_excerpt is not None and len(self.tone_source_excerpt) > 512:
            raise ValueError("trecho de origem de tone não pode exceder 512 caracteres")
        if (self.general_goal is None) != (self.goal_source_session_id is None):
            raise ValueError("origem de general_goal deve acompanhar seu valor")
        if (self.general_goal is None) != (self.goal_source_interaction_id is None):
            raise ValueError("interação de origem de general_goal deve acompanhar seu valor")
        if self.general_goal is None and self.goal_source_excerpt is not None:
            raise ValueError("trecho de origem de general_goal exige valor")
        if self.goal_source_excerpt is not None and len(self.goal_source_excerpt) > 512:
            raise ValueError("trecho de origem de general_goal não pode exceder 512 caracteres")

    @property
    def is_empty(self) -> bool:
        return self.tone is None and self.general_goal is None

    @property
    def tone_effective_updated_at(self) -> datetime:
        return self.tone_updated_at or self.updated_at

    @property
    def goal_effective_updated_at(self) -> datetime:
        return self.goal_updated_at or self.updated_at

    @property
    def summary_text(self) -> str | None:
        fields = []
        if self.tone:
            fields.append(f"Tom de comunicação: {self.tone}")
        if self.general_goal:
            fields.append(f"Objetivo geral: {self.general_goal}")
        return "; ".join(fields) if fields else None
