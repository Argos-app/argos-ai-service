"""Factory explícita dos agentes locais sem dependência do aplicativo principal."""

from datetime import UTC, datetime

from argos_a2a_agent.adapters.observability.recording import observe_adapter
from argos_a2a_agent.agents.registry import DocumentAgentRegistry
from argos_a2a_agent.agents.specialists import (
    Clock,
    DocumentalAgent,
    LegalAgent,
    MixedDocumentAgent,
)
from argos_a2a_agent.application.document_queries import SearchDocuments
from argos_a2a_agent.application.ensure_current_corpus import EnsureCurrentCorpus
from argos_a2a_agent.ports.document_answer_generator import DocumentAnswerGenerator
from argos_a2a_agent.ports.document_read_repository import DocumentReadRepository
from argos_a2a_agent.ports.embedding_provider import EmbeddingProvider
from packages.contracts.a2a import DocumentSkill
from packages.observability import ObservationCollector


class DocumentAgentFactory:
    """Monta os agentes com portas explícitas e compartilhadas por requisição."""

    @staticmethod
    def create(
        *,
        ensure_current: EnsureCurrentCorpus,
        repository: DocumentReadRepository,
        embeddings: EmbeddingProvider,
        answer_generator: DocumentAnswerGenerator,
        clock: Clock = lambda: datetime.now(UTC),
        observation_collector: ObservationCollector | None = None,
    ) -> DocumentAgentRegistry:
        search = observe_adapter(
            SearchDocuments(ensure_current, repository, embeddings),
            observation_collector,
            operation_prefix="document.search",
        )
        documental = DocumentalAgent(
            search,
            ensure_current,
            repository,
            answer_generator,
            clock=clock,
            observation_collector=observation_collector,
        )
        legal = LegalAgent(
            search,
            ensure_current,
            repository,
            answer_generator,
            clock=clock,
            observation_collector=observation_collector,
        )
        mixed = MixedDocumentAgent(
            search,
            ensure_current,
            repository,
            answer_generator,
            documental,
            legal,
            clock=clock,
            observation_collector=observation_collector,
        )
        return DocumentAgentRegistry(
            {
                DocumentSkill.DOCUMENTAL: documental,
                DocumentSkill.LEGAL: legal,
            },
            mixed,
        )
