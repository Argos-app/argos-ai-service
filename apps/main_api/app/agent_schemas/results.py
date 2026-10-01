"""Schemas tipados para roteamento, respostas especialistas e julgamento."""

from enum import StrEnum

from app.agent_schemas.inputs import QuestionFormattingRequest
from app.ports.agent import AgentId
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SpecialistAgentId(StrEnum):
    """IDs que o roteador pode atribuir como tarefa especialista."""

    FARM = AgentId.FARM.value
    DOCTOR = AgentId.DOCTOR.value
    ANALYST = AgentId.ANALYST.value
    SECRETARY = AgentId.SECRETARY.value
    INFORMATION = AgentId.INFORMATION.value


class SpecialistStatus(StrEnum):
    """Estados previstos pelo fluxo e aceitos pelos prompts atuais."""

    SUCCESS = "success"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    NEEDS_CLARIFICATION = "needs_clarification"
    ERROR = "error"
    ABSTAIN = "abstain"


class JudgeDecision(StrEnum):
    """Decisão do agente Juiz sobre a resposta consolidada."""

    APPROVED = "approved"
    REVISE = "revise"
    REJECTED = "rejected"


class SourceReference(_AgentOutput):
    title: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    excerpt: str | None = None


class SpecialistResult(_AgentOutput):
    agent: SpecialistAgentId
    status: SpecialistStatus
    answer: str = ""
    sources: tuple[SourceReference, ...] = ()
    missing_fields: tuple[str, ...] = ()
    suggested_followups: tuple[QuestionFormattingRequest, ...] = ()
    error: str | None = None


class JudgeResult(_AgentOutput):
    decision: JudgeDecision
    reason: str = Field(min_length=1)
    unsupported_claims: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_approval(self) -> JudgeResult:
        if self.decision == JudgeDecision.APPROVED and self.unsupported_claims:
            raise ValueError("approved não pode conter afirmações sem fonte")
        return self


class RouteTask(_AgentOutput):
    """Tarefa individual produzida pelo roteador."""

    task_id: str = Field(min_length=1)
    agent: SpecialistAgentId
    goal: str = Field(min_length=1)
    brief_sanitized: str = Field(min_length=1)
    context_refs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()


class RoutePlan(_AgentOutput):
    """Plano ordenável de tarefas especialistas para uma solicitação."""

    intent: str = Field(min_length=1)
    tasks: tuple[RouteTask, ...] = ()
    missing_slots: tuple[str, ...] = ()


class QuestionFormatterResult(_AgentOutput):
    """Texto final da pergunta; IDs e envelope público são atribuídos pela API."""

    prompt: str = Field(min_length=1, max_length=8000)
