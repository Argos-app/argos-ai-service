"""Porta de leitura vetorial interna; não oferece mutação ou seleção de coleção."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class QdrantReader(Protocol):
    async def query_points(
        self,
        vector: Sequence[float],
        *,
        query_filter: Mapping[str, Any],
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def retrieve(self, point_ids: Sequence[str | int]) -> Sequence[Mapping[str, Any]]: ...

    async def count(self, *, query_filter: Mapping[str, Any]) -> int: ...

    async def scroll(
        self,
        *,
        query_filter: Mapping[str, Any],
        limit: int,
        offset: str | int | None = None,
    ) -> tuple[Sequence[Mapping[str, Any]], str | int | None]:
        """Percorre pontos em páginas limitadas, sem expor operações de escrita."""
