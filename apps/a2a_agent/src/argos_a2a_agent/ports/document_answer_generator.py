"""Porta para geração de linguagem com evidências e resultado estruturados."""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from argos_a2a_agent.domain.agent_answers import (
    AgentEvidence,
    GeneratedDocumentAnswer,
    SpecialistAnswerSummary,
)


class DocumentAnswerGenerator(Protocol):
    async def generate(
        self,
        *,
        system_prompt: str,
        question: str,
        jurisdiction: str | None,
        reference_date: date | None,
        evidence: Sequence[AgentEvidence],
        specialist_answers: Sequence[SpecialistAnswerSummary] = (),
    ) -> GeneratedDocumentAnswer:
        """Gera resposta estruturada; evidências e citações são validadas pelo serviço."""
