"""Contratos de entrada e saída das interações."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.application.contracts.base import ApplicationModel


class QuestionOption(ApplicationModel):
    option_id: str
    label: str


class MessageInteraction(ApplicationModel):
    kind: Literal["message"]
    client_message_id: UUID
    message: str = Field(min_length=1, max_length=8000)


class AnswerText(ApplicationModel):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=8000)


class AnswerChoice(ApplicationModel):
    kind: Literal["choice"]
    option_id: str = Field(min_length=1, max_length=128)


class AnswerConfirmation(ApplicationModel):
    kind: Literal["confirmation"]
    confirm: bool


AnswerValue = Annotated[
    AnswerText | AnswerChoice | AnswerConfirmation,
    Field(discriminator="kind"),
]


class AnswerInteraction(ApplicationModel):
    kind: Literal["answer"]
    interaction_id: UUID
    question_id: UUID
    answer: AnswerValue


InteractionInput = Annotated[
    MessageInteraction | AnswerInteraction,
    Field(discriminator="kind"),
]


class QuestionResponse(ApplicationModel):
    kind: Literal["question"]
    question_id: UUID
    input_type: Literal["free_text", "single_choice", "confirmation"]
    prompt: str
    reason_code: str
    options: list[QuestionOption] = Field(default_factory=list)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_options(self) -> QuestionResponse:
        if self.input_type == "single_choice" and not self.options:
            raise ValueError("single_choice exige pelo menos uma opção")
        if self.input_type != "single_choice" and self.options:
            raise ValueError("opções só são permitidas para single_choice")
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option_id deve ser único dentro da pergunta")
        return self


class AnswerResponse(ApplicationModel):
    kind: Literal["answer"]
    text: str


ResponseValue = Annotated[
    AnswerResponse | QuestionResponse,
    Field(discriminator="kind"),
]


class InteractionResult(ApplicationModel):
    schema_version: Literal["1"] = "1"
    status: Literal["completed", "degraded"] = "completed"
    degradation_reasons: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    session_id: UUID
    interaction_id: UUID
    public_request_id: UUID
    response: ResponseValue


class InteractionAccepted(ApplicationModel):
    operation_id: UUID
    status: Literal["accepted"] = "accepted"
    poll_after_ms: int = 1000


class InteractionOperationStatus(ApplicationModel):
    operation_id: UUID
    status: Literal["accepted", "running", "completed", "degraded", "failed", "interrupted"]
    result: InteractionResult | None = None
