"""Repositório PostgreSQL somente leitura para identidade e acesso a fazendas."""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from app.domain.user_access import UserAccess, UserFarm

_USER_ACCESS_SELECT = """
    SELECT
        u.id AS user_id,
        u.firebase_uid,
        u.name AS user_name,
        u.email AS user_email,
        u.active AS user_active,
        u.blocked_until,
        pm.code AS permission_code,
        pm.name AS permission_name,
        pm.active AS permission_active,
        p.id AS farm_id,
        p.name AS farm_name
    FROM app_user AS u
    JOIN permission AS pm ON pm.id = u.permission_id
    LEFT JOIN property AS p
      ON p.active = TRUE
     AND u.active = TRUE
     AND pm.active = TRUE
     AND (u.blocked_until IS NULL OR u.blocked_until <= CURRENT_TIMESTAMP)
     AND (
         p.user_id = u.id
         OR EXISTS (
             SELECT 1
             FROM user_property AS up
             WHERE up.user_id = u.id
               AND up.property_id = p.id
               AND up.active = TRUE
               AND up.link_date <= CURRENT_DATE
               AND (up.unlink_date IS NULL OR up.unlink_date >= CURRENT_DATE)
         )
     )
    WHERE {identity_predicate}
    ORDER BY p.name, p.id
"""


class ReadOnlySqlReader:
    """Executa consultas parametrizadas em transações explicitamente read-only."""

    def __init__(self, pool: Any, *, acquire_timeout_seconds: float) -> None:
        self._pool = pool
        self._acquire_timeout_seconds = acquire_timeout_seconds

    async def fetch_all(self, query: str, *parameters: Any) -> Sequence[Mapping[str, Any]]:
        async with self._pool.acquire(timeout=self._acquire_timeout_seconds) as connection:
            async with connection.transaction(readonly=True):
                records = await connection.fetch(query, *parameters)
        return tuple(dict(record) for record in records)


class PostgresUserAccessRepository:
    """Carrega cadastro e fazendas próprias ou vinculadas atualmente ao usuário."""

    def __init__(self, sql_reader: ReadOnlySqlReader) -> None:
        self._sql_reader = sql_reader

    async def find_by_firebase_uid(self, firebase_uid: str) -> UserAccess | None:
        if not firebase_uid or not firebase_uid.strip():
            raise ValueError("firebase_uid não pode estar vazio")
        records = await self._sql_reader.fetch_all(
            _USER_ACCESS_SELECT.format(identity_predicate="u.firebase_uid = $1"),
            firebase_uid,
        )
        return _user_access_from_records(records)

    async def find_by_user_id(self, user_id: UUID) -> UserAccess | None:
        records = await self._sql_reader.fetch_all(
            _USER_ACCESS_SELECT.format(identity_predicate="u.id = $1"),
            user_id,
        )
        return _user_access_from_records(records)


def _user_access_from_records(
    records: Sequence[Mapping[str, Any]],
) -> UserAccess | None:
    if not records:
        return None

    first = records[0]
    farms = tuple(
        UserFarm(farm_id=UUID(str(record["farm_id"])), name=str(record["farm_name"]))
        for record in records
        if record["farm_id"] is not None
    )
    return UserAccess(
        user_id=UUID(str(first["user_id"])),
        firebase_uid=(str(first["firebase_uid"]) if first["firebase_uid"] is not None else None),
        name=str(first["user_name"]),
        email=str(first["user_email"]),
        active=bool(first["user_active"]),
        blocked_until=first["blocked_until"],
        permission_code=str(first["permission_code"]),
        permission_name=str(first["permission_name"]),
        permission_active=bool(first["permission_active"]),
        farms=farms,
    )
