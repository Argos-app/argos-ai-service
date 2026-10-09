"""Revalidação relacional de usuário e propriedades atualmente acessíveis."""

from uuid import UUID

from argos_mcp_prediction.ports.sql_reader import SqlReader


class PostgresAccessDirectory:
    def __init__(self, sql_reader: SqlReader) -> None:
        self._sql_reader = sql_reader

    async def get_active_property_ids(self, user_id: UUID) -> set[UUID]:
        records = await self._sql_reader.fetch_all(
            """
            SELECT DISTINCT p.id AS property_id
            FROM app_user AS u
            JOIN permission AS pm ON pm.id = u.permission_id
            JOIN property AS p
              ON p.active = TRUE
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
            WHERE u.id = $1
              AND u.active = TRUE
              AND pm.active = TRUE
              AND (u.blocked_until IS NULL OR u.blocked_until <= CURRENT_TIMESTAMP)
            """,
            user_id,
        )
        return {UUID(str(record["property_id"])) for record in records}
