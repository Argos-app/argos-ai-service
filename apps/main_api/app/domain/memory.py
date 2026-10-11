"""Modelos para resumos de sessão e perfil persistente do usuário."""

from dataclasses import dataclass
from datetime import datetime
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

    def __post_init__(self) -> None:
        require_utc(self.updated_at, "updated_at")
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
        if (self.general_goal is None) != (self.goal_source_session_id is None):
            raise ValueError("origem de general_goal deve acompanhar seu valor")
        if (self.general_goal is None) != (self.goal_source_interaction_id is None):
            raise ValueError("interação de origem de general_goal deve acompanhar seu valor")

    @property
    def is_empty(self) -> bool:
        return self.tone is None and self.general_goal is None
