"""Porta para persistir e recuperar referências de origem de relatório."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.report_artifacts import ReportArtifact


class ReportArtifactRepository(Protocol):
    async def save(self, artifact: ReportArtifact) -> None: ...

    async def list_for_task(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        task_id: str,
    ) -> Sequence[ReportArtifact]: ...

    async def get(
        self,
        reference: UUID,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        task_id: str,
    ) -> ReportArtifact: ...
