"""Artefatos confiáveis de relatório mantidos fora do conteúdo dos agentes."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from packages.contracts import LotReportEnvelope


@dataclass(frozen=True, slots=True)
class ReportArtifact:
    reference: UUID
    envelope: LotReportEnvelope
    principal_user_id: UUID
    session_id: UUID
    interaction_id: UUID
    task_id: str
    created_at: datetime

    @property
    def expires_at(self) -> datetime:
        return self.envelope.valid_until


class ReportArtifactNotFoundError(LookupError):
    """A referência não existe ou não pertence ao contexto confiável informado."""


class ReportArtifactExpiredError(ReportArtifactNotFoundError):
    """A origem assinada vinculada à referência expirou."""
