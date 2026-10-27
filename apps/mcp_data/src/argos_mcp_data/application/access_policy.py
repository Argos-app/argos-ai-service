"""Revalidação de contexto e redução de seleção ao universo autorizado."""

from datetime import UTC, datetime
from uuid import UUID

from argos_mcp_data.domain import AccessContext, AuthorizedScope
from argos_mcp_data.domain.errors import AccessDeniedError
from argos_mcp_data.ports.access_directory import AccessDirectory


class AccessPolicy:
    def __init__(self, access_directory: AccessDirectory) -> None:
        self._access_directory = access_directory

    async def authorize(
        self,
        context: AccessContext | None,
        *,
        operation: str,
        selected_property_ids: tuple[UUID, ...] | None = None,
    ) -> AuthorizedScope:
        if context is None:
            raise AccessDeniedError("contexto de acesso obrigatório")
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise AccessDeniedError("contexto de acesso expirado")
        user_access = await self._access_directory.get_user_property_access(context.firebase_uid)
        if user_access is None:
            raise AccessDeniedError("usuário sem acesso ativo")
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise AccessDeniedError("contexto de acesso expirado")
        return AuthorizedScope.build(
            context,
            user_id=user_access.user_id,
            current_property_ids=user_access.property_ids,
            operation=operation,
            selected_property_ids=selected_property_ids,
        )
