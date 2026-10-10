"""Portas internas do serviço documental A2A."""

from argos_a2a_agent.ports.corpus_synchronizer import CorpusSynchronizer
from argos_a2a_agent.ports.document_index_writer import DocumentIndexWriter
from argos_a2a_agent.ports.document_read_repository import DocumentReadRepository
from argos_a2a_agent.ports.document_source import DocumentSource
from argos_a2a_agent.ports.embedding_provider import EmbeddingProvider
from argos_a2a_agent.ports.manifest_repository import ManifestRepository
from argos_a2a_agent.ports.pdf_parser import PdfParser
from argos_a2a_agent.ports.qdrant_reader import QdrantReader

__all__ = [
    "CorpusSynchronizer",
    "DocumentIndexWriter",
    "DocumentReadRepository",
    "DocumentSource",
    "EmbeddingProvider",
    "ManifestRepository",
    "PdfParser",
    "QdrantReader",
]
