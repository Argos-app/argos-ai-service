"""Contratos da consolidação e da revisão de evidências."""

from app.agent_schemas.results import RouteTask, SourceReference, SpecialistResult
from pydantic import BaseModel, ConfigDict, Field


class _SynthesisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TaskOutcome(_SynthesisModel):
    task: RouteTask
    result: SpecialistResult


class OrchestratorInput(_SynthesisModel):
    request: str = Field(min_length=1)
    outcomes: tuple[TaskOutcome, ...]
    revision_feedback: str | None = None


class OrchestratorResult(_SynthesisModel):
    draft_answer: str = Field(min_length=1)
    sources: tuple[SourceReference, ...] = ()


class JudgeInput(_SynthesisModel):
    request: str = Field(min_length=1)
    draft_answer: str = Field(min_length=1)
    sources: tuple[SourceReference, ...] = ()
    outcomes: tuple[TaskOutcome, ...] = ()
