"""Providers determinísticos para desenvolvimento sem credenciais de modelos."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import date

from argos_a2a_agent.domain.agent_answers import (
    AgentEvidence,
    GeneratedDocumentAnswer,
    SpecialistAnswerSummary,
)
from packages.contracts.a2a import DocumentAnswerStatus


class SimulatedEmbeddingProvider:
    """Gera vetores lexicais determinísticos para buscas locais reproduzíveis.

    O hashing por termo preserva similaridade lexical entre consulta e documento;
    não tenta simular compreensão semântica de um provider de embeddings.
    """

    _STOP_WORDS = frozenset(
        {
            "a",
            "à",
            "ao",
            "aos",
            "as",
            "com",
            "como",
            "da",
            "das",
            "de",
            "do",
            "dos",
            "e",
            "em",
            "entre",
            "essa",
            "esse",
            "esta",
            "este",
            "eu",
            "foi",
            "há",
            "isso",
            "isto",
            "me",
            "na",
            "nas",
            "no",
            "nos",
            "o",
            "os",
            "ou",
            "para",
            "pela",
            "pelas",
            "pelo",
            "pelos",
            "que",
            "se",
            "sem",
            "ser",
            "sobre",
            "um",
            "uma",
            "umas",
            "uns",
            "é",
            "qual",
            "quais",
            "quando",
            "onde",
            "por",
            "porque",
            "the",
            "and",
            "for",
            "from",
            "with",
            "what",
        }
    )

    def __init__(self, vector_size: int) -> None:
        if isinstance(vector_size, bool) or not isinstance(vector_size, int) or vector_size < 1:
            raise ValueError("vector_size deve ser inteiro positivo")
        self._vector_size = vector_size

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("o lote simulado de embeddings deve conter textos")
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> tuple[float, ...]:
        normalized = unicodedata.normalize("NFKD", text.casefold())
        normalized = "".join(
            character for character in normalized if not unicodedata.combining(character)
        )
        terms = [
            term
            for term in re.findall(r"[a-z0-9]+", normalized)
            if len(term) > 1 and term not in self._STOP_WORDS
        ]
        if not terms:
            terms = [term for term in re.findall(r"[a-z0-9]+", normalized) if len(term) > 1]
        counts = Counter(terms)
        vector = [0.0] * self._vector_size
        for term, count in counts.items():
            digest = hashlib.sha256(term.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._vector_size
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        if not any(vector):
            digest = hashlib.sha256(normalized.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % self._vector_size] = 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return tuple(value / norm for value in vector)


class SimulatedAnswerGenerator:
    """Devolve os trechos recuperados, sem inventar síntese semântica."""

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
        cited_evidence = tuple(item for item in evidence[:5] if item.source.excerpt.strip())
        cited_ids = tuple(item.evidence_id for item in cited_evidence)
        if not cited_ids:
            return GeneratedDocumentAnswer(
                answer="",
                status=DocumentAnswerStatus.ABSTAIN,
                limitations=("O modo simulado não encontrou evidências para citar.",),
            )
        return GeneratedDocumentAnswer(
            answer=(
                "Trechos encontrados no corpus (modo simulado):\n"
                + "\n".join(
                    f"- {item.source.title}, p. {item.source.page_from}–{item.source.page_to}: "
                    f"{item.source.excerpt[:800].strip()} [[{item.evidence_id}]]"
                    for item in cited_evidence
                )
            ),
            status=DocumentAnswerStatus.SUCCESS,
            cited_evidence_ids=cited_ids,
            limitations=("Texto ilustrativo; não representa análise de um modelo de linguagem.",),
        )
