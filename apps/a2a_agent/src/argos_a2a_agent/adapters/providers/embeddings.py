"""Adapter do endpoint OpenAI-compatible de embeddings."""

import math
from collections.abc import Sequence
from typing import Any

from argos_a2a_agent.adapters.providers.http import OpenAICompatibleHTTP
from argos_a2a_agent.domain.errors import EmbeddingProviderError


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        *,
        max_batch_size: int = 128,
    ) -> None:
        if not 1 <= max_batch_size <= 128:
            raise ValueError("max_batch_size deve estar entre 1 e 128")
        self._http = OpenAICompatibleHTTP(endpoint, api_key, timeout_seconds)
        self._model = model
        self._max_batch_size = max_batch_size

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts or len(texts) > self._max_batch_size:
            raise EmbeddingProviderError("lote de embeddings vazio ou acima do limite")
        if any(
            not isinstance(text, str) or not text.strip() or len(text) > 32_000 for text in texts
        ):
            raise EmbeddingProviderError("texto de embedding vazio ou acima do limite")
        try:
            result = await self._http.post_json({"model": self._model, "input": list(texts)})
            vectors = _ordered_vectors(result, len(texts))
        except Exception as exc:
            if isinstance(exc, EmbeddingProviderError):
                raise
            raise EmbeddingProviderError("provedor de embeddings indisponível") from exc
        return vectors

    async def aclose(self) -> None:
        await self._http.aclose()


def _ordered_vectors(result: dict[str, Any], expected_count: int) -> tuple[tuple[float, ...], ...]:
    data = result.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise ValueError("quantidade de vetores inválida")
    indexed: dict[int, tuple[float, ...]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("vetor inválido")
        index = item.get("index")
        raw_vector = item.get("embedding")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < expected_count:
            raise ValueError("índice de embedding inválido")
        if index in indexed or not isinstance(raw_vector, list) or not raw_vector:
            raise ValueError("vetor vazio ou índice repetido")
        if len(raw_vector) > 65_536:
            raise ValueError("dimensão do vetor excede o limite")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_vector
        ):
            raise ValueError("vetor contém valor que não é número")
        vector = tuple(float(value) for value in raw_vector)
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("vetor contém valor não finito")
        if indexed and len(vector) != len(next(iter(indexed.values()))):
            raise ValueError("dimensões de vetores inconsistentes")
        indexed[index] = vector
    if set(indexed) != set(range(expected_count)):
        raise ValueError("resposta não contém todos os índices")
    return tuple(indexed[index] for index in range(expected_count))
