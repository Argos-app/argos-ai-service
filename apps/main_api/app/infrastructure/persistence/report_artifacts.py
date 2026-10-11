"""Persistência Mongo de envelopes de relatório fora do estado visível aos agentes."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.report_artifacts import (
    ReportArtifact,
    ReportArtifactExpiredError,
    ReportArtifactNotFoundError,
)


def _document(artifact: ReportArtifact) -> dict[str, Any]:
    return {
        "_id": str(artifact.reference),
        "principal_user_id": str(artifact.principal_user_id),
        "session_id": str(artifact.session_id),
        "interaction_id": str(artifact.interaction_id),
        "task_id": artifact.task_id,
        "created_at": artifact.created_at,
        "expires_at": artifact.expires_at,
        "purge_at": artifact.expires_at + timedelta(days=1),
        "envelope": artifact.envelope.model_dump(mode="json"),
    }


def _artifact(document: dict[str, Any]) -> ReportArtifact:
    from packages.contracts import LotReportEnvelope

    return ReportArtifact(
        reference=UUID(document["_id"]),
        envelope=LotReportEnvelope.model_validate(document["envelope"]),
        principal_user_id=UUID(document["principal_user_id"]),
        session_id=UUID(document["session_id"]),
        interaction_id=UUID(document["interaction_id"]),
        task_id=document["task_id"],
        created_at=document["created_at"],
    )


class MongoReportArtifactRepository:
    """Guarda envelope intacto e exige o vínculo completo em cada leitura."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("principal_user_id", 1), ("session_id", 1), ("interaction_id", 1), ("task_id", 1)],
            name="ix_report_artifacts_execution",
        )
        await self._collection.create_index(
            "purge_at",
            name="ttl_report_artifacts",
            expireAfterSeconds=0,
        )

    async def save(self, artifact: ReportArtifact) -> None:
        await self._collection.delete_many(
            {
                "principal_user_id": str(artifact.principal_user_id),
                "session_id": str(artifact.session_id),
                "interaction_id": str(artifact.interaction_id),
                "task_id": artifact.task_id,
            }
        )
        await self._collection.insert_one(_document(artifact))

    async def list_for_task(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        task_id: str,
    ) -> tuple[ReportArtifact, ...]:
        cursor = self._collection.find(
            {
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
                "interaction_id": str(interaction_id),
                "task_id": task_id,
            }
        ).sort([("created_at", -1), ("_id", 1)])
        return tuple(_artifact(document) async for document in cursor)

    async def get(
        self,
        reference: UUID,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        task_id: str,
    ) -> ReportArtifact:
        document = await self._collection.find_one(
            {
                "_id": str(reference),
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
                "interaction_id": str(interaction_id),
                "task_id": task_id,
            }
        )
        if document is None:
            raise ReportArtifactNotFoundError("referência de relatório inválida")
        artifact = _artifact(document)
        if datetime.now(UTC) >= artifact.expires_at:
            raise ReportArtifactExpiredError("origem de relatório expirada")
        return artifact

    async def get_owned(
        self, *, reference: UUID, principal_user_id: UUID, session_id: UUID
    ) -> ReportArtifact:
        document = await self._collection.find_one(
            {
                "_id": str(reference),
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
            }
        )
        if document is None:
            raise ReportArtifactNotFoundError("referência de relatório inválida")
        artifact = _artifact(document)
        if datetime.now(UTC) >= artifact.expires_at:
            raise ReportArtifactExpiredError("origem de relatório expirada")
        return artifact


__all__ = ["MongoReportArtifactRepository"]
