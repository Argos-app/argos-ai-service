"""Contratos da consolidação e da revisão de evidências."""

from collections.abc import Mapping
from typing import Literal

from app.agent_schemas.inputs import SessionContext
from app.agent_schemas.results import RouteTask, SourceReference, SpecialistResult
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _SynthesisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _SynthesisOutput(BaseModel):
    # Ignore unused fields emitted by provider models; required answer fields
    # and nested source contracts remain validated.
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


class EmailDispatchEvidence(_SynthesisModel):
    """Resultado confiável do MCP, separado do texto produzido pelo agente."""

    status: Literal["pending", "accepted", "partial", "failed", "unknown"]
    confirmed: bool
    recovered: bool = False
    operation_id: str | None = None
    report_id: str | None = None
    recipient_count: int | None = Field(default=None, ge=0)
    message: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_confirmation(self) -> EmailDispatchEvidence:
        if self.confirmed != (self.status != "unknown"):
            raise ValueError("confirmed deve corresponder ao estado MCP conhecido")
        return self


class TaskOutcome(_SynthesisModel):
    task: RouteTask
    result: SpecialistResult
    email_dispatch: EmailDispatchEvidence | None = None


class OrchestratorInput(_SynthesisModel):
    request: str = Field(min_length=1)
    outcomes: tuple[TaskOutcome, ...]
    revision_feedback: str | None = None
    session_context: SessionContext = Field(default_factory=SessionContext)


class OrchestratorResult(_SynthesisOutput):
    draft_answer: str = Field(min_length=1)
    sources: tuple[SourceReference, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_fields(cls, value):
        """Aceita nomes de resposta comuns; referências continuam validadas depois."""
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        if "draft_answer" not in normalized:
            for alias in ("answer", "response", "text"):
                candidate = normalized.get(alias)
                if isinstance(candidate, str) and candidate.strip():
                    normalized["draft_answer"] = candidate
                    break
        if "sources" not in normalized and "citations" in normalized:
            normalized["sources"] = normalized["citations"]
        return normalized


class JudgeInput(_SynthesisModel):
    request: str = Field(min_length=1)
    draft_answer: str = Field(min_length=1)
    sources: tuple[SourceReference, ...] = ()
    outcomes: tuple[TaskOutcome, ...] = ()
