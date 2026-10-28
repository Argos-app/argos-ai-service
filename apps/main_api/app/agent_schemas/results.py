"""Schemas tipados para roteamento, respostas especialistas e julgamento."""

from collections.abc import Mapping
from enum import StrEnum

from app.agent_schemas.inputs import QuestionFormattingRequest
from app.ports.agent import AgentId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _AgentOutput(BaseModel):
    """Ignore campos extras inofensivos do provider e valide todos os campos usados pela aplicação."""

    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


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
    excerpt: str | None = Field(default=None, max_length=48_000)


class SpecialistResult(_AgentOutput):
    agent: SpecialistAgentId
    status: SpecialistStatus
    answer: str = ""
    sources: tuple[SourceReference, ...] = ()
    missing_fields: tuple[str, ...] = ()
    suggested_followups: tuple[QuestionFormattingRequest, ...] = ()
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_fields(cls, value):
        """Normaliza aliases usuais sem relaxar os estados nem as evidências."""
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        if "answer" not in normalized:
            for alias in ("response", "result", "text", "summary"):
                candidate = normalized.get(alias)
                if isinstance(candidate, str):
                    normalized["answer"] = candidate
                    break
        if "sources" not in normalized and "citations" in normalized:
            normalized["sources"] = normalized["citations"]
        missing_fields = normalized.get("missing_fields")
        if isinstance(missing_fields, str):
            normalized["missing_fields"] = (missing_fields,) if missing_fields.strip() else ()
        return normalized


class JudgeResult(_AgentOutput):
    decision: JudgeDecision
    reason: str = Field(default="Decisão registrada sem justificativa detalhada.", min_length=1)
    unsupported_claims: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_fields(cls, value):
        """Aceita nomes comuns de veredito sem afrouxar os valores possíveis."""
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        if "decision" not in normalized:
            for alias in ("verdict", "status", "decision_status"):
                if alias in normalized:
                    normalized["decision"] = normalized[alias]
                    break
        if "reason" not in normalized:
            for alias in ("rationale", "feedback", "explanation"):
                candidate = normalized.get(alias)
                if isinstance(candidate, str) and candidate.strip():
                    normalized["reason"] = candidate
                    break
        if "unsupported_claims" not in normalized:
            for alias in ("unsupported", "claims_without_support"):
                if alias in normalized:
                    normalized["unsupported_claims"] = normalized[alias]
                    break
        unsupported = normalized.get("unsupported_claims")
        if isinstance(unsupported, str):
            normalized["unsupported_claims"] = (unsupported,) if unsupported.strip() else ()
        return normalized

    @field_validator("decision", mode="before")
    @classmethod
    def normalize_decision(cls, value):
        if not isinstance(value, str):
            return value
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        aliases = {
            "approve": JudgeDecision.APPROVED,
            "approved": JudgeDecision.APPROVED,
            "accept": JudgeDecision.APPROVED,
            "accepted": JudgeDecision.APPROVED,
            "pass": JudgeDecision.APPROVED,
            "ok": JudgeDecision.APPROVED,
            "aprovado": JudgeDecision.APPROVED,
            "aprovada": JudgeDecision.APPROVED,
            "revise": JudgeDecision.REVISE,
            "revision": JudgeDecision.REVISE,
            "needs_revision": JudgeDecision.REVISE,
            "revisar": JudgeDecision.REVISE,
            "corrigir": JudgeDecision.REVISE,
            "reject": JudgeDecision.REJECTED,
            "rejected": JudgeDecision.REJECTED,
            "deny": JudgeDecision.REJECTED,
            "denied": JudgeDecision.REJECTED,
            "fail": JudgeDecision.REJECTED,
            "rejeitado": JudgeDecision.REJECTED,
            "rejeitada": JudgeDecision.REJECTED,
            "reprovado": JudgeDecision.REJECTED,
            "reprovada": JudgeDecision.REJECTED,
        }
        return aliases.get(normalized, normalized)

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
    required_evidence: tuple[str, ...] = Field(
        default=(),
        description=(
            "Evidências que a tarefa deve produzir. Para uma nova preparação de relatório, use "
            "signed_report_source for Farm and confirmed_email_preparation for Secretary."
        ),
    )


class RoutePlan(_AgentOutput):
    """Plano ordenável de tarefas especialistas para uma solicitação."""

    intent: str = Field(min_length=1)
    tasks: tuple[RouteTask, ...] = ()
    missing_slots: tuple[str, ...] = ()


class QuestionFormatterResult(_AgentOutput):
    """Pergunta e alternativas gerais; IDs e envelope são atribuídos pela API."""

    prompt: str = Field(min_length=1, max_length=8000)
    options: tuple[str, ...] = Field(default=(), max_length=6)

    @field_validator("options")
    @classmethod
    def validate_option_labels(cls, options: tuple[str, ...]) -> tuple[str, ...]:
        labels = tuple(label.strip() for label in options)
        if any(not label or len(label) > 160 for label in labels):
            raise ValueError("cada opção deve ter entre 1 e 160 caracteres")
        if len({label.casefold() for label in labels}) != len(labels):
            raise ValueError("opções devem ser únicas")
        return labels

    @model_validator(mode="after")
    def validate_options(self) -> QuestionFormatterResult:
        if self.options and len(self.options) < 2:
            raise ValueError("pergunta de escolha exige ao menos duas opções")
        return self
