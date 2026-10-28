"""Revalidação de contexto e redução de seleção ao universo autorizado."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from packages.security.access import AccessContext, AuthorizedScope
from packages.security.access_directory import AccessDirectory
from packages.security.errors import AccessDeniedError


class BaseAccessPolicy[Scope]:
    def __init__(
        self,
        access_directory: AccessDirectory,
        *,
        scope_builder: Callable[..., Scope],
        denied_error: type[Exception],
    ) -> None:
        self._access_directory = access_directory
        self._scope_builder = scope_builder
        self._denied_error = denied_error

    async def authorize(
        self,
        context: AccessContext | None,
        *,
        operation: str,
        selected_property_ids: tuple[UUID, ...] | None = None,
    ) -> Scope:
        if context is None:
            raise self._denied_error("contexto de acesso obrigatório")
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise self._denied_error("contexto de acesso expirado")
        user_access = await self._access_directory.get_user_property_access(context.firebase_uid)
        if user_access is None:
            raise self._denied_error("usuário sem acesso ativo")
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise self._denied_error("contexto de acesso expirado")
        return self._scope_builder(
            context,
            user_id=user_access.user_id,
            current_property_ids=user_access.property_ids,
            operation=operation,
            selected_property_ids=selected_property_ids,
        )


class AccessPolicy(BaseAccessPolicy[AuthorizedScope]):
    def __init__(self, access_directory: AccessDirectory) -> None:
        super().__init__(
            access_directory,
            scope_builder=AuthorizedScope.build,
            denied_error=AccessDeniedError,
        )
