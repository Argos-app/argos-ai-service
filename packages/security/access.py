"""Contexto imutável de delegação e escopo efetivo por consulta."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from packages.security.errors import AccessDeniedError


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
class DocumentAccessContext:
    """Delegação de leitura ao catálogo global, sem escopo de propriedades."""

    user_id: UUID
    operations: frozenset[str]
    agent_id: str
    issued_at: datetime
    expires_at: datetime
    token_id: str
    request_id: str
    correlation_id: UUID | None = None
    parent_observation_id: UUID | None = None
    session_id: UUID | None = None
    interaction_id: UUID | None = None


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
            max_selection=100,
            allow_duplicate_claims=False,
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
        )


def select_authorized_properties(
    context: AccessContext,
    *,
    current_property_ids: set[UUID],
    operation: str,
    selected_property_ids: tuple[UUID, ...] | None,
    max_selection: int | None,
    allow_duplicate_claims: bool,
    denied_error: type[Exception],
) -> tuple[UUID, ...]:
    """Aplica regras compartilhadas preservando os limites de cada serviço."""
    if context.expires_at.tzinfo is None or context.expires_at <= datetime.now(UTC):
        raise denied_error("contexto de acesso expirado")
    if operation not in context.operations:
        raise denied_error("operação fora do contexto autorizado")
    claimed = set(context.authorized_property_ids)
    if (
        not claimed
        or (not allow_duplicate_claims and len(claimed) != len(context.authorized_property_ids))
        or not claimed.issubset(current_property_ids)
    ):
        raise denied_error("contexto de propriedades desatualizado")

    selection = (
        context.authorized_property_ids if selected_property_ids is None else selected_property_ids
    )
    if (
        not selection
        or (max_selection is not None and len(selection) > max_selection)
        or len(set(selection)) != len(selection)
    ):
        raise denied_error("seleção de propriedades inválida")
    if not set(selection).issubset(claimed):
        raise denied_error("seleção de propriedades fora do escopo autorizado")
    return tuple(selection)
