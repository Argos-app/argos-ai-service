"""Port tipada do agente que redige perguntas para o usuário."""

from typing import Protocol

from app.agent_schemas.inputs import QuestionFormatterInput
from app.agent_schemas.results import QuestionFormatterResult
from app.ports.agent import Agent


class QuestionFormatter(Agent[QuestionFormatterInput, QuestionFormatterResult], Protocol):
    """Formata uma pergunta a partir de uma solicitação estruturada e validada."""
