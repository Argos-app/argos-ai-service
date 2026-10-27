"""Schemas tipados usados como entradas dos agentes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ContextMessage(_AgentInput):
    """Mensagem pública anterior, incluída apenas para continuidade da conversa."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class SessionContext(_AgentInput):
    """Contexto persistido da sessão e preferências permitidas do usuário."""

    session_summary: str | None = Field(default=None, max_length=12_000)
    recent_messages: tuple[ContextMessage, ...] = ()
    user_tone: str | None = Field(default=None, max_length=256)
    user_general_goal: str | None = Field(default=None, max_length=1_000)


class PseudonymizedInput(_AgentInput):
    """Mensagem já pseudonimizada que pode seguir para o fluxo de agentes."""

    message: str = Field(min_length=1)


class RouterInput(_AgentInput):
    """Informação mínima para o roteador decompor e encaminhar uma solicitação."""

    message: str = Field(min_length=1)
    session_context: SessionContext = Field(default_factory=SessionContext)


class DependencyEvidence(_AgentInput):
    """Trecho validado da evidência que sustenta um resultado dependente."""

    title: str = Field(min_length=1, max_length=160)
    reference: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=48_000)


class DependencyResult(_AgentInput):
    """Resultado anterior disponibilizado a uma tarefa dependente."""

    task_id: str = Field(min_length=1)
    answer: str
    source_references: tuple[str, ...] = ()
    source_evidence: tuple[DependencyEvidence, ...] = ()
    report_artifact_refs: tuple[str, ...] = ()


class SpecialistInput(_AgentInput):
    """Tarefa sanitizada encaminhada pelo roteador a um agente especialista."""

    task_id: str = Field(min_length=1)
    request: str | None = Field(default=None, min_length=1, max_length=8000)
    goal: str = Field(min_length=1)
    brief_sanitized: str = Field(min_length=1)
    context_refs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    dependency_results: tuple[DependencyResult, ...] = ()
    session_context: SessionContext = Field(default_factory=SessionContext)
    email_preparation_count: int | None = Field(default=None, ge=0, le=20)
    email_send_authorized: bool = False


class QuestionOption(_AgentInput):
    """Opção estruturada com identificador atribuído pelo serviço."""

    option_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1)


class QuestionFormattingRequest(_AgentInput):
    """Intenção da pergunta e opções confiáveis, quando o fluxo já as definiu."""

    reason_code: str = Field(min_length=1)
    input_type: Literal["free_text", "single_choice", "confirmation"] | None = None
    question_goal: str = Field(min_length=1)
    options: tuple[QuestionOption, ...] = ()

    @model_validator(mode="after")
    def validate_options(self) -> QuestionFormattingRequest:
        if self.input_type == "single_choice" and not self.options:
            raise ValueError("single_choice exige pelo menos uma opção")
        if self.input_type != "single_choice" and self.options:
            raise ValueError("opções só são permitidas para single_choice")
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option_id deve ser único dentro da pergunta")
        return self


class QuestionFormatterInput(_AgentInput):
    """Contexto sanitizado para formular uma pergunta de esclarecimento."""

    request: QuestionFormattingRequest
    user_request: str = Field(min_length=1, max_length=8_000)
