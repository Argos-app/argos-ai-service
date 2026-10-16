"""Providers determinísticos para desenvolvimento sem credenciais de modelos."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import date

from argos_a2a_agent.domain.agent_answers import (
    AgentEvidence,
    GeneratedDocumentAnswer,
    SpecialistAnswerSummary,
)
from packages.contracts.a2a import DocumentAnswerStatus


class SimulatedEmbeddingProvider:
    """Gera vetores estáveis de dimensão configurada, sem alegar qualidade semântica."""

    def __init__(self, vector_size: int) -> None:
        if isinstance(vector_size, bool) or not isinstance(vector_size, int) or vector_size < 1:
            raise ValueError("vector_size deve ser inteiro positivo")
        self._vector_size = vector_size

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("o lote simulado de embeddings deve conter textos")
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> tuple[float, ...]:
        values: list[float] = []
        counter = 0
        while len(values) < self._vector_size:
            block = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in block)
            counter += 1
        vector = values[: self._vector_size]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return tuple(value / norm for value in vector)


class SimulatedAnswerGenerator:
    """Responde apenas com uma frase fixa e cita evidências recebidas do serviço."""

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
        del system_prompt, question, jurisdiction, reference_date, specialist_answers
        cited_ids = tuple(item.evidence_id for item in evidence[:20])
        if not cited_ids:
            return GeneratedDocumentAnswer(
                answer="",
                status=DocumentAnswerStatus.ABSTAIN,
                limitations=("O modo simulado não encontrou evidências para citar.",),
            )
        return GeneratedDocumentAnswer(
            answer=(
                "Resposta gerada em modo simulado com base nos trechos recuperados. "
                + " ".join(f"[[{evidence_id}]]" for evidence_id in cited_ids)
            ),
            status=DocumentAnswerStatus.SUCCESS,
            cited_evidence_ids=cited_ids,
            limitations=("Texto ilustrativo; não representa análise de um modelo de linguagem.",),
        )
