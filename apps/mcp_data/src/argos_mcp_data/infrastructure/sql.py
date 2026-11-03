"""Adapter PostgreSQL limitado a operações de leitura."""

from collections.abc import Mapping, Sequence
from typing import Any


class AsyncpgSqlReader:
    """Leitor asyncpg; o pool força transações somente leitura no PostgreSQL."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def fetch_all(self, query: str, *parameters: Any) -> Sequence[Mapping[str, Any]]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SET TRANSACTION READ ONLY")
                records = await connection.fetch(query, *parameters)
        return tuple(dict(record) for record in records)
