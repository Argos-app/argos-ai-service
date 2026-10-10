"""Contrato local de execução dos agentes documentais."""

from typing import Protocol

from packages.contracts.a2a import (
    DocumentAnswerResponse,
    DocumentQuestionRequest,
    DocumentSkill,
)
from packages.security.access import DocumentAccessContext


class DocumentAgent(Protocol):
    skill: DocumentSkill

    async def execute(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
    ) -> DocumentAnswerResponse: ...
