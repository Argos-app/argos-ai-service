"""Providers locais determinísticos do runtime A2A."""

import pytest
from argos_a2a_agent.adapters.providers.simulated import (
    SimulatedAnswerGenerator,
    SimulatedEmbeddingProvider,
)
from argos_a2a_agent.domain.agent_answers import AgentEvidence

from packages.contracts.a2a import DocumentAnswerStatus, DocumentSourceReference, DocumentType


@pytest.mark.asyncio
async def test_simulated_embeddings_are_stable_and_match_configured_dimension():
    provider = SimulatedEmbeddingProvider(64)

    first = await provider.embed(("texto de teste",))
    second = await provider.embed(("texto de teste",))

    assert first == second
    assert len(first[0]) == 64


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
            excerpt="Trecho de demonstração.",
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
