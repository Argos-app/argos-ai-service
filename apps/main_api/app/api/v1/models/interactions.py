"""Contratos de entrada e saída das interações."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.application.contracts.base import ApplicationModel


class QuestionOption(ApplicationModel):
    """Opção de resposta identificada pelo serviço."""

    option_id: str = Field(
        description="Identificador estável da opção, usado ao responder à pergunta.",
        examples=["option-1"],
    )
    label: str = Field(
        description="Texto apresentado ao usuário para esta opção.", examples=["Lote 12"]
    )


class MessageInteraction(ApplicationModel):
    """Nova mensagem enviada pelo usuário à sessão."""

    kind: Literal["message"] = Field(description="Discriminador da interação; use `message`.")
    client_message_id: UUID = Field(
        description="Identificador gerado pelo cliente para deduplicar o envio.",
        examples=["123e4567-e89b-12d3-a456-426614174001"],
    )
    message: str = Field(
        min_length=1,
        max_length=8000,
        description="Texto da mensagem do usuário, com até 8.000 caracteres.",
        examples=["Quantos animais há no lote 12?"],
    )


class AnswerText(ApplicationModel):
    """Resposta em texto livre a uma pergunta pendente."""

    kind: Literal["text"] = Field(description="Discriminador da resposta; use `text`.")
    text: str = Field(
        min_length=1,
        max_length=8000,
        description="Texto fornecido pelo usuário, com até 8.000 caracteres.",
        examples=["Lote 12"],
    )


class AnswerChoice(ApplicationModel):
    """Seleção de uma opção de resposta oferecida pelo serviço."""

    kind: Literal["choice"] = Field(description="Discriminador da resposta; use `choice`.")
    option_id: str = Field(
        min_length=1,
        max_length=128,
        description="option_id de uma das opções da pergunta pendente.",
        examples=["option-1"],
    )


class AnswerConfirmation(ApplicationModel):
    """Confirmação ou recusa de uma ação solicitada."""

    kind: Literal["confirmation"] = Field(
        description="Discriminador da resposta; use `confirmation`."
    )
    confirm: bool = Field(
        description="Use true para confirmar ou false para recusar.", examples=[True]
    )


AnswerValue = Annotated[
    AnswerText | AnswerChoice | AnswerConfirmation,
    Field(discriminator="kind"),
]


class AnswerInteraction(ApplicationModel):
    """Resposta vinculada à pergunta de esclarecimento de uma interação anterior."""

    kind: Literal["answer"] = Field(description="Discriminador da interação; use `answer`.")
    interaction_id: UUID = Field(
        description="Identificador da interação que apresentou a pergunta.",
        examples=["123e4567-e89b-12d3-a456-426614174002"],
    )
    question_id: UUID = Field(
        description="Identificador da pergunta que está sendo respondida.",
        examples=["123e4567-e89b-12d3-a456-426614174003"],
    )
    answer: AnswerValue = Field(description="Resposta em texto, seleção ou confirmação.")


InteractionInput = Annotated[
    MessageInteraction | AnswerInteraction,
    Field(discriminator="kind"),
]


class QuestionResponse(ApplicationModel):
    """Pergunta de esclarecimento que aguarda uma resposta do usuário."""

    kind: Literal["question"] = Field(description="Discriminador da resposta; use `question`.")
    question_id: UUID = Field(
        description="Identificador usado para vincular a resposta à pergunta.",
        examples=["123e4567-e89b-12d3-a456-426614174003"],
    )
    input_type: Literal["free_text", "single_choice", "confirmation"] = Field(
        description="Tipo de resposta esperado: texto livre, opção única ou confirmação.",
        examples=["single_choice"],
    )
    prompt: str = Field(
        description="Pergunta apresentada ao usuário.", examples=["Qual lote você quer consultar?"]
    )
    reason_code: str = Field(
        description="Código técnico do motivo do esclarecimento.", examples=["MISSING_FIELDS"]
    )
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="Opções apresentadas quando input_type é `single_choice`.",
    )
    expires_at: datetime = Field(
        description="Prazo de validade da pergunta em UTC.",
        examples=["2026-10-27T12:10:00Z"],
    )

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
    """Resposta final apresentada ao usuário."""

    kind: Literal["answer"] = Field(description="Discriminador da resposta; use `answer`.")
    text: str = Field(
        description="Texto final da resposta.", examples=["O lote 12 contém 48 animais."]
    )


ResponseValue = Annotated[
    AnswerResponse | QuestionResponse,
    Field(discriminator="kind"),
]


class InteractionResult(ApplicationModel):
    """Resultado final de uma interação processada."""

    schema_version: Literal["1"] = Field(default="1", description="Versão do contrato de resposta.")
    status: Literal["completed", "degraded"] = Field(
        default="completed",
        description="Estado da resposta: concluída ou concluída com limitações.",
        examples=["completed"],
    )
    degradation_reasons: list[str] = Field(
        default_factory=list,
        description="Códigos das limitações que afetaram a resposta, quando houver.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Data e hora de conclusão em UTC.",
        examples=["2026-10-27T12:00:03Z"],
    )
    session_id: UUID = Field(description="Identificador da sessão.")
    interaction_id: UUID = Field(description="Identificador da interação processada.")
    public_request_id: UUID = Field(description="Identificador público desta requisição.")
    response: ResponseValue = Field(description="Pergunta de esclarecimento ou resposta final.")


class InteractionAccepted(ApplicationModel):
    """Confirmação de que o processamento assíncrono foi aceito."""

    operation_id: UUID = Field(
        description="Identificador usado para consultar o estado da operação.",
        examples=["123e4567-e89b-12d3-a456-426614174004"],
    )
    status: Literal["accepted"] = Field(
        default="accepted", description="Estado inicial da operação.", examples=["accepted"]
    )
    poll_after_ms: int = Field(
        default=1000,
        description="Intervalo mínimo sugerido, em milissegundos, antes de consultar novamente.",
        examples=[1000],
    )


class InteractionOperationStatus(ApplicationModel):
    """Estado atual de uma operação de interação assíncrona."""

    operation_id: UUID = Field(description="Identificador da operação consultada.")
    status: Literal["accepted", "running", "completed", "degraded", "failed", "interrupted"] = (
        Field(
            description="Estado do processamento da interação.",
            examples=["running"],
        )
    )
    result: InteractionResult | None = Field(
        default=None,
        description="Resultado disponível após a conclusão; omitido enquanto não houver resultado.",
    )
