"""Casos de uso da aplicação A2A."""

from argos_a2a_agent.application.ensure_current_corpus import (
    EnsureCurrentCorpus,
    EnsureCurrentCorpusOutcome,
)
from argos_a2a_agent.application.prepare_document import PrepareDocumentForIndex
from argos_a2a_agent.application.text_chunker import TextChunker

__all__ = [
    "EnsureCurrentCorpus",
    "EnsureCurrentCorpusOutcome",
    "PrepareDocumentForIndex",
    "TextChunker",
]
