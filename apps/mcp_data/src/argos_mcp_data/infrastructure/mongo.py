"""Adapter Motor com superfície exclusivamente de leitura."""

from collections.abc import Mapping, Sequence
from typing import Any


class ReadOnlyMongoReader:
    """Expõe find/find_one e não oferece métodos de mutação ou DDL."""

    def __init__(self, database: Any) -> None:
        self._database = database

    async def find_one(
        self,
        collection: str,
        filter: Mapping[str, Any],
        projection: Mapping[str, int] | None = None,
    ) -> Mapping[str, Any] | None:
        return await self._database[collection].find_one(filter, projection)

    async def find_many(
        self,
        collection: str,
        filter: Mapping[str, Any],
        *,
        projection: Mapping[str, int] | None = None,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit deve estar entre 1 e 1000")
        cursor = self._database[collection].find(filter, projection).limit(limit)
        return tuple(await cursor.to_list(length=limit))
