"""Persistência Mongo das respostas completas de preparação de relatório."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from app.domain.email_preparations import EmailPreparation


def _document(preparation: EmailPreparation) -> dict[str, Any]:
    return {
        "_id": str(preparation.reference),
        "principal_user_id": str(preparation.principal_user_id),
        "session_id": str(preparation.session_id),
        "interaction_id": str(preparation.interaction_id),
        "report_artifact_ref": str(preparation.report_artifact_ref),
        "request_fingerprint": preparation.request_fingerprint,
        "created_at": preparation.created_at,
        "expires_at": preparation.prepared_report.expires_at,
        "purge_at": preparation.prepared_report.expires_at + timedelta(days=1),
        "prepared_report": preparation.prepared_report.model_dump(mode="json"),
    }


def _preparation(document: dict[str, Any]) -> EmailPreparation:
    from packages.contracts import PreparedReport

    return EmailPreparation(
        reference=UUID(document["_id"]),
        principal_user_id=UUID(document["principal_user_id"]),
        session_id=UUID(document["session_id"]),
        interaction_id=UUID(document["interaction_id"]),
        report_artifact_ref=UUID(document["report_artifact_ref"]),
        request_fingerprint=document["request_fingerprint"],
        prepared_report=PreparedReport.model_validate(document["prepared_report"]),
        created_at=document["created_at"],
    )


class MongoEmailPreparationRepository:
    """Armazena conteúdo e recibo completo com leitura vinculada ao principal."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [
                ("principal_user_id", 1),
                ("session_id", 1),
                ("report_artifact_ref", 1),
                ("request_fingerprint", 1),
            ],
            unique=True,
            name="uq_email_preparation_request",
        )
        await self._collection.create_index(
            "purge_at",
            name="ttl_email_preparations",
            expireAfterSeconds=0,
        )

    async def find_matching(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        report_artifact_ref: UUID,
        request_fingerprint: str,
    ) -> EmailPreparation | None:
        document = await self._collection.find_one(
            {
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
                "report_artifact_ref": str(report_artifact_ref),
                "request_fingerprint": request_fingerprint,
            }
        )
        return _preparation(document) if document is not None else None

    async def save(self, preparation: EmailPreparation) -> None:
        await self._collection.replace_one(
            {
                "principal_user_id": str(preparation.principal_user_id),
                "session_id": str(preparation.session_id),
                "report_artifact_ref": str(preparation.report_artifact_ref),
                "request_fingerprint": preparation.request_fingerprint,
            },
            _document(preparation),
            upsert=True,
        )


__all__ = ["MongoEmailPreparationRepository"]
