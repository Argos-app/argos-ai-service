"""Pool PostgreSQL somente leitura para dados cadastrais do main_api."""

from dataclasses import dataclass
from typing import Any

from app.infrastructure.postgres_user_access import (
    PostgresUserAccessRepository,
    ReadOnlySqlReader,
)
from app.settings import Settings


@dataclass(slots=True)
class PostgresUserAccessRuntime:
    pool: Any
    user_access_repository: PostgresUserAccessRepository

    async def close(self) -> None:
        await self.pool.close()


async def create_postgres_user_access_runtime(
    settings: Settings,
) -> PostgresUserAccessRuntime | None:
    """Cria o pool e verifica o acesso às tabelas cadastrais."""
    if settings.auth_database_url is None:
        return None

    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeError("instale a dependência asyncpg para usar o cadastro PostgreSQL") from exc

    query_timeout_ms = round(settings.auth_database_query_timeout_seconds * 1000)
    pool = await asyncpg.create_pool(
        settings.auth_database_url,
        min_size=settings.auth_database_pool_min_size,
        max_size=settings.auth_database_pool_max_size,
        timeout=settings.auth_database_connect_timeout_seconds,
        command_timeout=settings.auth_database_query_timeout_seconds,
        server_settings={
            "application_name": "argos-main-api",
            "default_transaction_read_only": "on",
            "statement_timeout": f"{query_timeout_ms}ms",
            "lock_timeout": f"{min(query_timeout_ms, 1_000)}ms",
            "idle_in_transaction_session_timeout": "5000ms",
        },
    )
    try:
        await _verify_select_access(pool, settings.auth_database_connect_timeout_seconds)
        reader = ReadOnlySqlReader(
            pool,
            acquire_timeout_seconds=settings.auth_database_connect_timeout_seconds,
        )
        return PostgresUserAccessRuntime(
            pool=pool,
            user_access_repository=PostgresUserAccessRepository(reader),
        )
    except Exception:
        await pool.close()
        raise


async def _verify_select_access(pool: Any, acquire_timeout_seconds: float) -> None:
    async with pool.acquire(timeout=acquire_timeout_seconds) as connection:
        async with connection.transaction(readonly=True):
            has_select_access = await connection.fetchval("""
                SELECT
                    has_table_privilege(current_user, 'app_user', 'SELECT')
                    AND has_table_privilege(current_user, 'permission', 'SELECT')
                    AND has_table_privilege(current_user, 'property', 'SELECT')
                    AND has_table_privilege(current_user, 'user_property', 'SELECT')
                    AND has_schema_privilege(current_user, current_schema(), 'USAGE')
                """)
            if not has_select_access:
                raise RuntimeError(
                    "a credencial PostgreSQL precisa de SELECT nas tabelas cadastrais"
                )
