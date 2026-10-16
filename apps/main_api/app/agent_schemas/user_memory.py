"""Saída estruturada para extrair somente preferências persistentes permitidas."""

from typing import Literal

from app.domain.memory import (
    MemoryFieldAction,
    UserMemoryChange,
    UserMemoryFieldChange,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryFieldDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: Literal["unchanged", "set", "delete"] = "unchanged"
    value: str | None = Field(default=None, max_length=1_000)
    source_excerpt: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_action(self) -> MemoryFieldDraft:
        if self.action == "set":
            if not self.value or not self.source_excerpt:
                raise ValueError("set exige value e source_excerpt")
        elif self.value is not None or self.source_excerpt is not None:
            raise ValueError("value e source_excerpt só são permitidos com set")
        return self

    def to_domain(self) -> UserMemoryFieldChange:
        return UserMemoryFieldChange(
            action=MemoryFieldAction(self.action),
            value=self.value,
            source_excerpt=self.source_excerpt,
        )


class UserMemoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tone: MemoryFieldDraft = Field(default_factory=MemoryFieldDraft)
    general_goal: MemoryFieldDraft = Field(default_factory=MemoryFieldDraft)

    @model_validator(mode="after")
    def validate_tone_length(self) -> UserMemoryDraft:
        if self.tone.value is not None and len(self.tone.value) > 256:
            raise ValueError("tone não pode exceder 256 caracteres")
        return self

    def to_domain(self) -> UserMemoryChange:
        return UserMemoryChange(
            tone=self.tone.to_domain(), general_goal=self.general_goal.to_domain()
        )
