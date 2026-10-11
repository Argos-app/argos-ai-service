"""Porta de armazenamento protegido das preparações do MCP de e-mail."""

from typing import Protocol
from uuid import UUID

from app.domain.email_preparations import (
    EmailPreparation,
    EmailSendAuthorization,
    PendingRecipientResolution,
)


class EmailSendAuthorizationConflictError(RuntimeError):
    """Uma interação já autorizou outra preparação ou outro conjunto de destinatários."""


class EmailPreparationRepository(Protocol):
    async def find_matching(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        report_artifact_ref: UUID,
        request_fingerprint: str,
    ) -> EmailPreparation | None: ...

    async def save(self, preparation: EmailPreparation) -> None: ...

    async def save_pending_recipient_resolution(
        self, resolution: PendingRecipientResolution
    ) -> None: ...

    async def get_pending_recipient_resolution(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        question_id: UUID | None = None,
    ) -> PendingRecipientResolution | None: ...

    async def reserve_send_authorization(
        self, authorization: EmailSendAuthorization
    ) -> EmailSendAuthorization: ...

    async def get_send_authorization(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        source_interaction_id: UUID,
    ) -> EmailSendAuthorization | None: ...
