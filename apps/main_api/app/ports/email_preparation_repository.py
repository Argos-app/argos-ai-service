"""Porta de armazenamento protegido das preparações do MCP de e-mail."""

from typing import Protocol
from uuid import UUID

from app.domain.email_preparations import EmailPreparation


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
