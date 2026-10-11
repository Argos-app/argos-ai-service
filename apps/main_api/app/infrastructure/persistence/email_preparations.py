"""Persistência Mongo das respostas completas de preparação de relatório."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.email_preparations import (
    EmailPreparation,
    PendingRecipientOption,
    PendingRecipientResolution,
)


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


def _pending_document(resolution: PendingRecipientResolution) -> dict[str, Any]:
    return {
        "_id": str(resolution.question_id),
        "principal_user_id": str(resolution.principal_user_id),
        "session_id": str(resolution.session_id),
        "interaction_id": str(resolution.interaction_id),
        "report_artifact_ref": str(resolution.report_artifact_ref),
        "input_type": resolution.input_type,
        "reason_code": resolution.reason_code,
        "question_goal": resolution.question_goal,
        "unresolved_selector": resolution.unresolved_selector.model_dump(mode="json"),
        "options": [
            {
                "option_id": option.option_id,
                "label": option.label,
                "selector": option.selector.model_dump(mode="json"),
            }
            for option in resolution.options
        ],
        "expires_at": resolution.expires_at,
        "purge_at": resolution.expires_at + timedelta(days=1),
    }


def _pending_resolution(document: dict[str, Any]) -> PendingRecipientResolution:
    from packages.contracts import RecipientSelector

    return PendingRecipientResolution(
        question_id=UUID(document["_id"]),
        principal_user_id=UUID(document["principal_user_id"]),
        session_id=UUID(document["session_id"]),
        interaction_id=UUID(document["interaction_id"]),
        report_artifact_ref=UUID(document["report_artifact_ref"]),
        input_type=document["input_type"],
        reason_code=document["reason_code"],
        question_goal=document["question_goal"],
        unresolved_selector=RecipientSelector.model_validate(document["unresolved_selector"]),
        options=tuple(
            PendingRecipientOption(
                option_id=option["option_id"],
                label=option["label"],
                selector=RecipientSelector.model_validate(option["selector"]),
            )
            for option in document["options"]
        ),
        expires_at=document["expires_at"],
    )


class MongoEmailPreparationRepository:
    """Armazena conteúdo e recibo completo com leitura vinculada ao principal."""

    def __init__(self, collection: Any, pending_resolutions: Any = None) -> None:
        self._collection = collection
        self._pending_resolutions = pending_resolutions

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
        if self._pending_resolutions is not None:
            await self._pending_resolutions.create_index(
                [("principal_user_id", 1), ("session_id", 1), ("interaction_id", 1)],
                unique=True,
                name="uq_pending_recipient_question_interaction",
            )
            await self._pending_resolutions.create_index(
                "purge_at",
                name="ttl_pending_recipient_questions",
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

    async def save_pending_recipient_resolution(
        self, resolution: PendingRecipientResolution
    ) -> None:
        if self._pending_resolutions is None:
            raise RuntimeError("armazenamento de perguntas de destinatário indisponível")
        await self._pending_resolutions.replace_one(
            {
                "principal_user_id": str(resolution.principal_user_id),
                "session_id": str(resolution.session_id),
                "interaction_id": str(resolution.interaction_id),
            },
            _pending_document(resolution),
            upsert=True,
        )

    async def get_pending_recipient_resolution(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        question_id: UUID | None = None,
    ) -> PendingRecipientResolution | None:
        if self._pending_resolutions is None:
            return None
        query: dict[str, Any] = {
            "principal_user_id": str(principal_user_id),
            "session_id": str(session_id),
            "interaction_id": str(interaction_id),
            "expires_at": {"$gt": datetime.now(UTC)},
        }
        if question_id is not None:
            query["_id"] = str(question_id)
        document = await self._pending_resolutions.find_one(query)
        return _pending_resolution(document) if document is not None else None


__all__ = ["MongoEmailPreparationRepository"]
