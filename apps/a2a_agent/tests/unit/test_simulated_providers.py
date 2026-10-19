"""Providers locais determinísticos do runtime A2A."""

import math

import pytest
from argos_a2a_agent.adapters.providers.simulated import (
    SimulatedAnswerGenerator,
    SimulatedEmbeddingProvider,
)
from argos_a2a_agent.domain.agent_answers import AgentEvidence

from packages.contracts.a2a import DocumentAnswerStatus, DocumentSourceReference, DocumentType


def test_simulated_embedding_uses_shared_terms_and_avoids_zero_vectors():
    provider = SimulatedEmbeddingProvider(256)
    query = provider._vector("dose da vacina bovina para febre")
    related = provider._vector("vacina bovina dose recomendada para febre")
    unrelated = provider._vector("contratos de arrendamento rural")
    related_score = sum(left * right for left, right in zip(query, related, strict=True))
    unrelated_score = sum(left * right for left, right in zip(query, unrelated, strict=True))
    minimal_query = provider._vector("o que é?")

    assert related_score > unrelated_score
    assert math.isclose(math.sqrt(sum(value * value for value in minimal_query)), 1.0)


@pytest.mark.asyncio
async def test_simulated_embeddings_are_stable_and_match_configured_dimension():
    provider = SimulatedEmbeddingProvider(64)

    first = await provider.embed(("texto de teste",))
    second = await provider.embed(("texto de teste",))

    assert first == second
    assert len(first[0]) == 64


@pytest.mark.asyncio
async def test_simulated_embeddings_rank_shared_terms_above_unrelated_text():
    provider = SimulatedEmbeddingProvider(256)
    query, related, unrelated = await provider.embed(
        (
            "dose da vacina bovina para febre",
            "vacina bovina dose recomendada para febre",
            "documento sobre contratos de arrendamento rural",
        )
    )
    related_score = sum(left * right for left, right in zip(query, related, strict=True))
    unrelated_score = sum(left * right for left, right in zip(query, unrelated, strict=True))

    assert related_score > unrelated_score


@pytest.mark.asyncio
async def test_simulated_answer_only_cites_evidence_given_by_the_service():
    evidence = AgentEvidence(
        evidence_id="ev_0123456789abcdef01234567",
        source=DocumentSourceReference(
            document_id="11111111-1111-4111-8111-111111111111",
            title="Documento sintético",
            document_type=DocumentType.LAW,
            origin="fixture://demo.pdf",
            revision_hash="a" * 64,
            page_from=1,
            page_to=1,
            excerpt="A vacina de demonstração deve ser mantida refrigerada entre 2 e 8 graus.",
            was_updated=False,
        ),
    )

    answer = await SimulatedAnswerGenerator().generate(
        system_prompt="",
        question="Pergunta de teste",
        jurisdiction=None,
        reference_date=None,
        evidence=(evidence,),
    )

    assert answer.status is DocumentAnswerStatus.SUCCESS
    assert answer.cited_evidence_ids == (evidence.evidence_id,)
    assert f"[[{evidence.evidence_id}]]" in answer.answer
    assert "mantida refrigerada entre 2 e 8 graus" in answer.answer
    assert "Resposta gerada em modo simulado" not in answer.answer
