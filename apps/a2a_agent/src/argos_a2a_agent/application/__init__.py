"""Casos de uso da aplicação A2A."""

from argos_a2a_agent.application.document_queries import (
    AggregateDocuments,
    GetDocumentExcerpt,
    ListDocuments,
    SearchDocuments,
)
from argos_a2a_agent.application.ensure_current_corpus import (
    EnsureCurrentCorpus,
    EnsureCurrentCorpusOutcome,
)
from argos_a2a_agent.application.prepare_document import PrepareDocumentForIndex
from argos_a2a_agent.application.text_chunker import TextChunker

__all__ = [
    "EnsureCurrentCorpus",
    "EnsureCurrentCorpusOutcome",
    "AggregateDocuments",
    "GetDocumentExcerpt",
    "ListDocuments",
    "PrepareDocumentForIndex",
    "SearchDocuments",
    "TextChunker",
]
