"""Contratos de entrada e saída das interações."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.application.contracts.base import ApplicationModel


class QuestionOptionResult(ApplicationModel):
    option_id: str
    label: str


class MessageCommand(ApplicationModel):
    kind: Literal["message"]
    client_message_id: UUID
    message: str = Field(min_length=1, max_length=8000)


class TextAnswer(ApplicationModel):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=8000)


class ChoiceAnswer(ApplicationModel):
    kind: Literal["choice"]
    option_id: str = Field(min_length=1, max_length=128)


class ConfirmationAnswer(ApplicationModel):
    kind: Literal["confirmation"]
    confirm: bool


AnswerCommandValue = Annotated[
    TextAnswer | ChoiceAnswer | ConfirmationAnswer,
    Field(discriminator="kind"),
]


class AnswerCommand(ApplicationModel):
    kind: Literal["answer"]
    interaction_id: UUID
    question_id: UUID
    answer: AnswerCommandValue


InteractionCommand = Annotated[
    MessageCommand | AnswerCommand,
    Field(discriminator="kind"),
]


class QuestionResult(ApplicationModel):
    kind: Literal["question"]
    question_id: UUID
    input_type: Literal["free_text", "single_choice", "confirmation"]
    prompt: str
    reason_code: str
    options: list[QuestionOptionResult] = Field(default_factory=list)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_options(self) -> QuestionResult:
        if self.input_type == "single_choice" and not self.options:
            raise ValueError("single_choice exige pelo menos uma opção")
        if self.input_type != "single_choice" and self.options:
            raise ValueError("opções só são permitidas para single_choice")
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option_id deve ser único dentro da pergunta")
        return self


class AnswerResult(ApplicationModel):
    kind: Literal["answer"]
    text: str


ApplicationResponse = Annotated[
    AnswerResult | QuestionResult,
    Field(discriminator="kind"),
]


class InteractionOutcome(ApplicationModel):
    schema_version: Literal["1"] = "1"
    status: Literal["completed", "degraded"] = "completed"
    degradation_reasons: tuple[str, ...] = ()
    session_id: UUID
    interaction_id: UUID
    public_request_id: UUID
    response: ApplicationResponse
