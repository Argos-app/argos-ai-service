"""Persistência Mongo das respostas completas de preparação de relatório."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.email_preparations import (
    EmailOperationReference,
    EmailPreparation,
    EmailSendAuthorization,
    PendingRecipientOption,
    PendingRecipientResolution,
    SendAuthorizationOrigin,
)
from app.ports.email_preparation_repository import EmailSendAuthorizationConflictError
from packages.contracts import DispatchStatus


def _document(preparation: EmailPreparation) -> dict[str, Any]:
    return {
        "_id": str(preparation.reference),
        "record_type": "preparation",
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
        "record_type": "pending_resolution",
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


def _send_authorization_document(authorization: EmailSendAuthorization) -> dict[str, Any]:
    return {
        "_id": str(authorization.authorization_id),
        "record_type": "authorization",
        "principal_user_id": str(authorization.principal_user_id),
        "session_id": str(authorization.session_id),
        "source_interaction_id": str(authorization.source_interaction_id),
        "preparation_reference": str(authorization.preparation_reference),
        "report_id": str(authorization.report_id),
        "request_fingerprint": authorization.request_fingerprint,
        "prepared_fingerprint": authorization.prepared_fingerprint,
        "recipients_fingerprint": authorization.recipients_fingerprint,
        "origin": authorization.origin.value,
        "idempotency_key": authorization.idempotency_key,
        "authorized_at": authorization.authorized_at,
        "expires_at": authorization.expires_at,
        "question_id": str(authorization.question_id) if authorization.question_id else None,
        "question_expires_at": authorization.question_expires_at,
        "purge_at": authorization.expires_at + timedelta(days=1),
    }


def _send_authorization(document: dict[str, Any]) -> EmailSendAuthorization:
    return EmailSendAuthorization(
        authorization_id=UUID(document["_id"]),
        principal_user_id=UUID(document["principal_user_id"]),
        session_id=UUID(document["session_id"]),
        source_interaction_id=UUID(document["source_interaction_id"]),
        preparation_reference=UUID(document["preparation_reference"]),
        report_id=UUID(document["report_id"]),
        request_fingerprint=document["request_fingerprint"],
        prepared_fingerprint=document["prepared_fingerprint"],
        recipients_fingerprint=document["recipients_fingerprint"],
        origin=SendAuthorizationOrigin(document["origin"]),
        idempotency_key=document["idempotency_key"],
        authorized_at=document["authorized_at"],
        expires_at=document["expires_at"],
        question_id=UUID(document["question_id"]) if document.get("question_id") else None,
        question_expires_at=document.get("question_expires_at"),
    )


def _operation_reference_document(reference: EmailOperationReference) -> dict[str, Any]:
    return {
        "_id": f"operation:{reference.authorization_id}",
        "record_type": "operation_reference",
        "principal_user_id": str(reference.principal_user_id),
        "session_id": str(reference.session_id),
        "operation_id": str(reference.operation_id),
        "recorded_at": reference.recorded_at,
        "last_known_status": (
            reference.last_known_status.value if reference.last_known_status is not None else None
        ),
        "last_known_updated_at": reference.last_known_updated_at,
        "purge_at": reference.recorded_at + timedelta(days=31),
    }


def _operation_reference(document: dict[str, Any]) -> EmailOperationReference:
    return EmailOperationReference(
        authorization_id=UUID(document["_id"].removeprefix("operation:")),
        principal_user_id=UUID(document["principal_user_id"]),
        session_id=UUID(document["session_id"]),
        operation_id=UUID(document["operation_id"]),
        recorded_at=document["recorded_at"],
        last_known_status=(
            DispatchStatus(document["last_known_status"])
            if document.get("last_known_status")
            else None
        ),
        last_known_updated_at=document.get("last_known_updated_at"),
    )


def _same_authorization(stored: EmailSendAuthorization, candidate: EmailSendAuthorization) -> bool:
    return all(
        getattr(stored, field) == getattr(candidate, field)
        for field in (
            "principal_user_id",
            "session_id",
            "source_interaction_id",
            "preparation_reference",
            "report_id",
            "request_fingerprint",
            "prepared_fingerprint",
            "recipients_fingerprint",
            "origin",
            "question_id",
            "question_expires_at",
        )
    )


class MongoEmailPreparationRepository:
    """Armazena conteúdo e recibo completo com leitura vinculada ao principal."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        for name, keys, record_type in (
            (
                "uq_email_workflow_preparation",
                [
                    ("principal_user_id", 1),
                    ("session_id", 1),
                    ("report_artifact_ref", 1),
                    ("request_fingerprint", 1),
                ],
                "preparation",
            ),
            (
                "uq_email_workflow_pending",
                [("principal_user_id", 1), ("session_id", 1), ("interaction_id", 1)],
                "pending_resolution",
            ),
            (
                "uq_email_workflow_authorization",
                [("principal_user_id", 1), ("session_id", 1), ("source_interaction_id", 1)],
                "authorization",
            ),
        ):
            await self._collection.create_index(
                keys,
                unique=True,
                name=name,
                partialFilterExpression={"record_type": record_type},
            )
        await self._collection.create_index(
            "purge_at", name="ttl_email_workflows", expireAfterSeconds=0
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
                "record_type": "preparation",
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
                "report_artifact_ref": str(report_artifact_ref),
                "request_fingerprint": request_fingerprint,
            }
        )
        return _preparation(document) if document is not None else None

    async def list_prepared_for_session(
        self, *, principal_user_id: UUID, session_id: UUID, limit: int = 20
    ) -> tuple[EmailPreparation, ...]:
        cursor = self._collection.find(
            {
                "record_type": "preparation",
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
                "prepared_report.status": "prepared",
            }
        )
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("created_at", -1)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(max(1, min(limit, 100)))
        return tuple([_preparation(document) async for document in cursor])

    async def save(self, preparation: EmailPreparation) -> None:
        await self._collection.replace_one(
            {
                "record_type": "preparation",
                "principal_user_id": str(preparation.principal_user_id),
                "session_id": str(preparation.session_id),
                "report_artifact_ref": str(preparation.report_artifact_ref),
                "request_fingerprint": preparation.request_fingerprint,
            },
            _document(preparation),
            upsert=True,
        )

    async def get_by_reference(
        self, *, reference: UUID, principal_user_id: UUID, session_id: UUID
    ) -> EmailPreparation | None:
        document = await self._collection.find_one(
            {
                "record_type": "preparation",
                "_id": str(reference),
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
            }
        )
        return _preparation(document) if document is not None else None

    async def save_pending_recipient_resolution(
        self, resolution: PendingRecipientResolution
    ) -> None:
        await self._collection.delete_one(
            {
                "record_type": "pending_resolution",
                "principal_user_id": str(resolution.principal_user_id),
                "session_id": str(resolution.session_id),
                "interaction_id": str(resolution.interaction_id),
                "_id": {"$ne": str(resolution.question_id)},
                "expires_at": {"$lte": datetime.now(UTC)},
            }
        )
        await self._collection.replace_one(
            {
                "record_type": "pending_resolution",
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
        query: dict[str, Any] = {
            "record_type": "pending_resolution",
            "principal_user_id": str(principal_user_id),
            "session_id": str(session_id),
            "interaction_id": str(interaction_id),
            "expires_at": {"$gt": datetime.now(UTC)},
        }
        if question_id is not None:
            query["_id"] = str(question_id)
        document = await self._collection.find_one(query)
        return _pending_resolution(document) if document is not None else None

    async def reserve_send_authorization(
        self, authorization: EmailSendAuthorization
    ) -> EmailSendAuthorization:
        scope = {
            "record_type": "authorization",
            "principal_user_id": str(authorization.principal_user_id),
            "session_id": str(authorization.session_id),
            "source_interaction_id": str(authorization.source_interaction_id),
        }
        document = _send_authorization_document(authorization)
        try:
            await self._collection.update_one(
                scope,
                {"$setOnInsert": document},
                upsert=True,
            )
        except Exception as exc:
            if not getattr(exc, "code", None) == 11000:
                raise
        stored_document = await self._collection.find_one(scope)
        if stored_document is None:
            raise RuntimeError("autorização de envio não foi persistida")
        stored = _send_authorization(stored_document)
        if not _same_authorization(stored, authorization):
            raise EmailSendAuthorizationConflictError(
                "a interação já está vinculada a outra preparação ou autorização"
            )
        return stored

    async def confirm_send_authorization(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        source_interaction_id: UUID,
        question_id: UUID,
        question_expires_at: datetime,
        now: datetime,
    ) -> EmailSendAuthorization | None:
        scope = {
            "record_type": "authorization",
            "principal_user_id": str(principal_user_id),
            "session_id": str(session_id),
            "source_interaction_id": str(source_interaction_id),
        }
        stored_document = await self._collection.find_one(scope)
        if stored_document is None:
            return None
        stored = _send_authorization(stored_document)
        if stored.origin == SendAuthorizationOrigin.CONFIRMED_QUESTION:
            if (
                stored.question_id == question_id
                and stored.question_expires_at == question_expires_at
            ):
                return stored
            raise EmailSendAuthorizationConflictError(
                "a solicitação já foi confirmada por outra pergunta"
            )
        if stored.origin != SendAuthorizationOrigin.EXPLICIT_REQUEST:
            raise EmailSendAuthorizationConflictError(
                "origem da autorização não pode ser confirmada"
            )
        if now >= question_expires_at or now >= stored.expires_at:
            raise EmailSendAuthorizationConflictError("a pergunta ou preparação expirou")

        confirmed = replace(
            stored,
            origin=SendAuthorizationOrigin.CONFIRMED_QUESTION,
            authorized_at=now,
            expires_at=min(stored.expires_at, question_expires_at),
            question_id=question_id,
            question_expires_at=question_expires_at,
        )
        document = _send_authorization_document(confirmed)
        update = {key: value for key, value in document.items() if key != "_id"}
        await self._collection.update_one(
            {**scope, "origin": SendAuthorizationOrigin.EXPLICIT_REQUEST.value},
            {"$set": update},
        )
        stored_document = await self._collection.find_one(scope)
        if stored_document is None:
            raise RuntimeError("autorização confirmada não foi persistida")
        stored = _send_authorization(stored_document)
        if not _same_authorization(stored, confirmed):
            raise EmailSendAuthorizationConflictError(
                "a autorização foi confirmada por uma pergunta diferente"
            )
        return stored

    async def get_send_authorization(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        source_interaction_id: UUID,
    ) -> EmailSendAuthorization | None:
        document = await self._collection.find_one(
            {
                "record_type": "authorization",
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
                "source_interaction_id": str(source_interaction_id),
            }
        )
        return _send_authorization(document) if document is not None else None

    async def list_send_authorizations(
        self, *, principal_user_id: UUID, session_id: UUID, limit: int = 20
    ) -> tuple[EmailSendAuthorization, ...]:
        cursor = self._collection.find(
            {
                "record_type": "authorization",
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
            }
        )
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("authorized_at", -1)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(max(1, min(limit, 100)))
        return tuple([_send_authorization(document) async for document in cursor])

    async def get_send_authorization_by_id(
        self, *, authorization_id: UUID, principal_user_id: UUID, session_id: UUID
    ) -> EmailSendAuthorization | None:
        document = await self._collection.find_one(
            {
                "record_type": "authorization",
                "_id": str(authorization_id),
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
            }
        )
        return _send_authorization(document) if document is not None else None

    async def get_send_operation_reference(
        self, *, authorization_id: UUID, principal_user_id: UUID, session_id: UUID
    ) -> EmailOperationReference | None:
        document = await self._collection.find_one(
            {
                "record_type": "operation_reference",
                "_id": f"operation:{authorization_id}",
                "principal_user_id": str(principal_user_id),
                "session_id": str(session_id),
            }
        )
        return _operation_reference(document) if document is not None else None

    async def save_send_operation_reference(
        self, reference: EmailOperationReference
    ) -> EmailOperationReference:
        scope = {
            "record_type": "operation_reference",
            "_id": f"operation:{reference.authorization_id}",
            "principal_user_id": str(reference.principal_user_id),
            "session_id": str(reference.session_id),
        }
        existing = await self._collection.find_one(scope)
        if existing is not None:
            stored = _operation_reference(existing)
            if stored.operation_id != reference.operation_id:
                raise EmailSendAuthorizationConflictError(
                    "a autorização já aponta para outra operação"
                )
            if reference.last_known_status is not None:
                await self._collection.update_one(
                    scope,
                    {
                        "$set": {
                            "last_known_status": reference.last_known_status.value,
                            "last_known_updated_at": reference.last_known_updated_at,
                        }
                    },
                )
            else:
                return stored
        else:
            await self._collection.update_one(
                scope,
                {"$setOnInsert": _operation_reference_document(reference)},
                upsert=True,
            )
        document = await self._collection.find_one(scope)
        if document is None:
            raise RuntimeError("referência da operação não foi persistida")
        stored = _operation_reference(document)
        if stored.operation_id != reference.operation_id:
            raise EmailSendAuthorizationConflictError("a autorização já aponta para outra operação")
        return stored


__all__ = ["MongoEmailPreparationRepository"]
