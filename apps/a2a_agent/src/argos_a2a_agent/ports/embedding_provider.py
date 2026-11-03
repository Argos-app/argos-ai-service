"""Porta de embeddings para lotes de texto extraído."""

from collections.abc import Sequence
from typing import Literal, Protocol


class EmbeddingProvider(Protocol):
    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: Literal["query", "document"] = "document",
    ) -> Sequence[Sequence[float]]:
        """Retorna um vetor por texto, preservando a ordem de entrada."""
