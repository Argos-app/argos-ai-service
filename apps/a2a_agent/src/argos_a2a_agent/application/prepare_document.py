"""Prepara um PDF allowlisted em chunks vetorizados sem publicar no índice."""

import math
from collections.abc import Sequence

from argos_a2a_agent.application.text_chunker import TextChunker
from argos_a2a_agent.domain.errors import EmbeddingProviderError, PdfExtractionError
from argos_a2a_agent.domain.models import IndexedDocumentChunk, SourceDocument
from argos_a2a_agent.ports.embedding_provider import EmbeddingProvider
from argos_a2a_agent.ports.pdf_parser import PdfParser


class PrepareDocumentForIndex:
    """Extrai, divide e vetoriza; não escreve em Qdrant nem altera a fonte."""

    def __init__(
        self,
        parser: PdfParser,
        embeddings: EmbeddingProvider,
        chunker: TextChunker | None = None,
        *,
        embedding_batch_size: int = 64,
    ) -> None:
        if not 1 <= embedding_batch_size <= 128:
            raise ValueError("embedding_batch_size deve estar entre 1 e 128")
        self._parser = parser
        self._embeddings = embeddings
        self._chunker = chunker or TextChunker()
        self._embedding_batch_size = embedding_batch_size

    async def execute(self, source: SourceDocument) -> tuple[IndexedDocumentChunk, ...]:
        try:
            parsed = await self._parser.parse(source.content, source.metadata)
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError("parser falhou ao processar o PDF allowlisted") from exc
        if parsed.metadata != source.metadata:
            raise PdfExtractionError("parser retornou metadados de outra revisão documental")
        chunks = self._chunker.split(parsed)

        indexed: list[IndexedDocumentChunk] = []
        dimensions: int | None = None
        for start in range(0, len(chunks), self._embedding_batch_size):
            batch = chunks[start : start + self._embedding_batch_size]
            try:
                vectors = await self._embeddings.embed([chunk.text for chunk in batch])
            except Exception as exc:
                raise EmbeddingProviderError("provedor de embeddings indisponível") from exc
            normalized = self._validate_vectors(vectors, len(batch), dimensions)
            dimensions = len(normalized[0])
            indexed.extend(
                IndexedDocumentChunk(chunk=chunk, vector=vector, metadata=source.metadata)
                for chunk, vector in zip(batch, normalized, strict=True)
            )
        return tuple(indexed)

    @staticmethod
    def _validate_vectors(
        vectors: Sequence[Sequence[float]], expected: int, expected_dimensions: int | None
    ) -> tuple[tuple[float, ...], ...]:
        try:
            if len(vectors) != expected:
                raise ValueError("quantidade de vetores diverge da quantidade de chunks")
            normalized = []
            dimensions = expected_dimensions
            for vector in vectors:
                if not vector or len(vector) > 8_192:
                    raise ValueError("dimensão do embedding vazia ou acima do limite")
                if dimensions is None:
                    dimensions = len(vector)
                if len(vector) != dimensions:
                    raise ValueError("dimensões de embeddings inconsistentes")
                values = tuple(float(value) for value in vector)
                if any(not math.isfinite(value) for value in values):
                    raise ValueError("embedding contém valor não finito")
                normalized.append(values)
            return tuple(normalized)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingProviderError("provedor retornou vetores inválidos") from exc
