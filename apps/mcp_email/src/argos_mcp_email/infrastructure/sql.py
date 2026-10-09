"""Adaptador SQL assíncrono baseado em um pool asyncpg."""

from typing import Any


class AsyncpgSqlExecutor:
    """Expõe o contrato SQL do domínio sobre um pool asyncpg."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def fetch_all(self, query: str, *parameters: Any) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(query, *parameters)
        return [dict(row) for row in rows]
