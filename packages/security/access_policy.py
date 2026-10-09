"""Revalidação de contexto e redução de seleção ao universo autorizado."""

from datetime import UTC, datetime
from uuid import UUID

from packages.security.access import AccessContext, AuthorizedScope
from packages.security.access_directory import AccessDirectory
from packages.security.errors import AccessDeniedError


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
        current_property_ids = await self._access_directory.get_active_property_ids(context.user_id)
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise AccessDeniedError("contexto de acesso expirado")
        return AuthorizedScope.build(
            context,
            current_property_ids=current_property_ids,
            operation=operation,
            selected_property_ids=selected_property_ids,
        )
