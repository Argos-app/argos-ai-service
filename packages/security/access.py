"""Contexto imutável de delegação e escopo efetivo por consulta."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from packages.security.errors import AccessDeniedError
from packages.security.limits import MAX_PROPERTY_IDS


@dataclass(frozen=True, slots=True)
class AccessContext:
    user_id: UUID
    authorized_property_ids: tuple[UUID, ...]
    operations: frozenset[str]
    agent_id: str
    issued_at: datetime
    expires_at: datetime
    token_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class DocumentAccessContext:
    """Delegação de leitura ao catálogo global, sem escopo de propriedades."""

    user_id: UUID
    operations: frozenset[str]
    agent_id: str
    issued_at: datetime
    expires_at: datetime
    token_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class AuthorizedScope:
    """Escopo já revalidado, obrigatório para leituras vinculadas a propriedade."""

    user_id: UUID
    property_ids: tuple[UUID, ...]
    operation: str
    agent_id: str
    token_id: str
    request_id: str
    expires_at: datetime

    @classmethod
    def build(
        cls,
        context: AccessContext,
        *,
        current_property_ids: set[UUID],
        operation: str,
        selected_property_ids: tuple[UUID, ...] | None = None,
    ) -> AuthorizedScope:
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise AccessDeniedError("contexto de acesso expirado")
        if operation not in context.operations:
            raise AccessDeniedError("operação fora do contexto autorizado")
        claimed = set(context.authorized_property_ids)
        if (
            not 1 <= len(context.authorized_property_ids) <= MAX_PROPERTY_IDS
            or len(claimed) != len(context.authorized_property_ids)
            or not claimed.issubset(current_property_ids)
        ):
            raise AccessDeniedError("contexto de propriedades desatualizado")

        selection = (
            context.authorized_property_ids
            if selected_property_ids is None
            else selected_property_ids
        )
        if not 1 <= len(selection) <= MAX_PROPERTY_IDS or len(set(selection)) != len(selection):
            raise AccessDeniedError("seleção de propriedades inválida")
        if not set(selection).issubset(claimed):
            raise AccessDeniedError("seleção de propriedades fora do escopo autorizado")

        return cls(
            user_id=context.user_id,
            property_ids=tuple(selection),
            operation=operation,
            agent_id=context.agent_id,
            token_id=context.token_id,
            request_id=context.request_id,
            expires_at=context.expires_at,
        )
