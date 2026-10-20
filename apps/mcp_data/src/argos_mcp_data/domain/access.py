"""Contexto imutável de delegação e escopo efetivo por consulta."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from argos_mcp_data.domain.errors import AccessDeniedError


@dataclass(frozen=True, slots=True)
class AccessContext:
    firebase_uid: str
    authorized_property_ids: tuple[UUID, ...]
    operations: frozenset[str]
    agent_id: str
    issued_at: datetime
    expires_at: datetime
    token_id: str
    request_id: str
    correlation_id: UUID | None = None
    parent_observation_id: UUID | None = None


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
    correlation_id: UUID | None = None
    parent_observation_id: UUID | None = None

    @classmethod
    def build(
        cls,
        context: AccessContext,
        *,
        user_id: UUID,
        current_property_ids: set[UUID],
        operation: str,
        selected_property_ids: tuple[UUID, ...] | None = None,
    ) -> AuthorizedScope:
        if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
            raise AccessDeniedError("contexto de acesso expirado")
        if operation not in context.operations:
            raise AccessDeniedError("operação fora do contexto autorizado")
        claimed = set(context.authorized_property_ids)
        if not claimed or not claimed.issubset(current_property_ids):
            raise AccessDeniedError("contexto de propriedades desatualizado")

        selection = (
            context.authorized_property_ids
            if selected_property_ids is None
            else selected_property_ids
        )
        if not selection or len(set(selection)) != len(selection):
            raise AccessDeniedError("seleção de propriedades inválida")
        if not set(selection).issubset(claimed):
            raise AccessDeniedError("seleção de propriedades fora do escopo autorizado")

        return cls(
            user_id=user_id,
            property_ids=tuple(selection),
            operation=operation,
            agent_id=context.agent_id,
            token_id=context.token_id,
            request_id=context.request_id,
            expires_at=context.expires_at,
            correlation_id=context.correlation_id,
            parent_observation_id=context.parent_observation_id,
        )
