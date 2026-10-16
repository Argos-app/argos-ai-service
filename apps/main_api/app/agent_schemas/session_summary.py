"""Contrato de saída estruturada do sumarizador de sessão."""

from pydantic import BaseModel, ConfigDict, Field


class SummaryFactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=1_000)
    source_references: tuple[str, ...] = Field(default=(), max_length=20)


class SessionSummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    activity: str = Field(min_length=1, max_length=3_000)
    important_facts: tuple[SummaryFactDraft, ...] = Field(default=(), max_length=40)
    decisions: tuple[str, ...] = Field(default=(), max_length=40)
    pending_activity: str | None = Field(default=None, max_length=1_000)
    limitations: tuple[str, ...] = Field(default=(), max_length=40)
