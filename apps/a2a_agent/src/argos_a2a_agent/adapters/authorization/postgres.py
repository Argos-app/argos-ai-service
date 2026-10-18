"""Revalida usuário e papel de leitura no PostgreSQL em cada operação documental."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from argos_a2a_agent.adapters.observability.recording import (
    OperationRecorder,
    active_collector,
)
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)


class PostgresDocumentPermissionDirectory:
    """Mapeia códigos de papel configurados para documents.read.

    A conexão é provisionada com default_transaction_read_only=on. A consulta
    também abre transação explicitamente somente leitura e usa parâmetros SQL.
    """

    _QUERY = """
        SELECT EXISTS (
            SELECT 1
            FROM app_user AS u
            JOIN permission AS p ON p.id = u.permission_id
            WHERE u.id = $1
              AND u.active = TRUE
              AND p.active = TRUE
              AND (u.blocked_until IS NULL OR u.blocked_until <= CURRENT_TIMESTAMP)
              AND p.code = ANY($2::varchar[])
              AND p.code <> 'SISTEMA'
        )
    """

    def __init__(
        self,
        pool: Any,
        role_codes: Sequence[str],
        *,
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        self._pool = pool
        self._role_codes = tuple(role_codes)
        if not self._role_codes or len(self._role_codes) != len(set(self._role_codes)):
            raise ValueError("lista de papéis para documents.read vazia ou duplicada")
        if "SISTEMA" in self._role_codes:
            raise ValueError("papel SISTEMA não pode receber documents.read")
        self._observation_collector = active_collector(observation_collector)

    async def has_active_documents_read_permission(self, user_id: UUID) -> bool:
        if not isinstance(user_id, UUID):
            return False
        recorder = (
            OperationRecorder(
                self._observation_collector,
                operation_kind=OperationKind.STORAGE,
                operation_name="postgres.documents_read_permission",
                operation_phase=OperationPhase.VALIDATION,
                observation_level=ObservationLevel.TRANSPORT_ATTEMPT,
                error_origin=ErrorOrigin.STORAGE,
            )
            if self._observation_collector is not None
            else None
        )
        try:
            async with self._pool.acquire() as connection:
                async with connection.transaction(readonly=True):
                    permitted = await connection.fetchval(
                        self._QUERY, user_id, list(self._role_codes)
                    )
        except BaseException as exc:
            if recorder is not None:
                recorder.finish(error=exc, error_origin=ErrorOrigin.STORAGE)
            raise
        if recorder is not None:
            recorder.finish(
                result=ResultMetadata(
                    source="postgres",
                    code="allowed" if permitted is True else "denied",
                )
            )
        return permitted is True

    async def aclose(self) -> None:
        await self._pool.close()
