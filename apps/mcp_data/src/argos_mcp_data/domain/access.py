"""Contexto imutável de delegação e escopo efetivo por consulta."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from argos_mcp_data.domain.errors import AccessDeniedError
from packages.security.access import AccessContext, select_authorized_properties


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
        selection = select_authorized_properties(
            context,
            current_property_ids=current_property_ids,
            operation=operation,
            selected_property_ids=selected_property_ids,
            max_selection=None,
            allow_duplicate_claims=True,
            denied_error=AccessDeniedError,
        )

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
