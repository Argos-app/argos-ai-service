"""Autorização atual para consultas ao catálogo global de documentos."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from packages.security.access import DocumentAccessContext
from packages.security.errors import AccessContextError, AccessDeniedError


class DocumentPermissionDirectory(Protocol):
    """Consulta a fonte vigente de identidade e permissão documental."""

    async def has_active_documents_read_permission(self, user_id: UUID) -> bool: ...


class DocumentAccessPolicy:
    """Revalida identidade/permissão sem aplicar filtros de propriedade."""

    def __init__(self, permission_directory: DocumentPermissionDirectory) -> None:
        self._permission_directory = permission_directory

    async def authorize(self, context: DocumentAccessContext | None) -> DocumentAccessContext:
        if not isinstance(context, DocumentAccessContext):
            raise AccessContextError("contexto documental obrigatório")
        now = datetime.now(UTC)
        if (
            context.issued_at.tzinfo is None
            or context.expires_at.tzinfo is None
            or context.issued_at > now
            or context.expires_at <= now
            or context.expires_at <= context.issued_at
        ):
            raise AccessContextError("contexto documental expirado ou inválido")
        if context.agent_id != "informacoes" or "documents.read" not in context.operations:
            raise AccessDeniedError("operação fora do contexto autorizado")
        if not await self._permission_directory.has_active_documents_read_permission(
            context.user_id
        ):
            raise AccessDeniedError("usuário sem permissão documental ativa")
        return context
