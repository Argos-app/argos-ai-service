"""Contrato mínimo de leitura documental, sem operações de escrita."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class MongoReader(Protocol):
    async def find_one(
        self,
        collection: str,
        filter: Mapping[str, Any],
        projection: Mapping[str, int] | None = None,
    ) -> Mapping[str, Any] | None: ...

    async def find_many(
        self,
        collection: str,
        filter: Mapping[str, Any],
        *,
        projection: Mapping[str, int] | None = None,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...
