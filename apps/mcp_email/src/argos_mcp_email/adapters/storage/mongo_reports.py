"""Persistência MongoDB de relatórios preparados."""

import base64
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from argos_mcp_email.domain import (
    PreparedReportRecord,
    ReportAlreadyExistsError,
    ReportAttachmentRecord,
    ReportState,
    ResolvedRecipient,
)


class MongoCollection(Protocol):
    async def insert_one(self, document: dict[str, Any]) -> Any: ...

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None: ...


class MongoReportRepository:
    """Implementa ``ReportRepository`` usando uma collection Mongo injetada."""

    def __init__(self, collection: MongoCollection) -> None:
        self._collection = collection

    async def save(self, report: PreparedReportRecord) -> None:
        document = self._to_document(report)
        try:
            await self._collection.insert_one(document)
        except Exception as exc:
            if getattr(exc, "code", None) == 11000:
                raise ReportAlreadyExistsError("relatório já foi salvo") from exc
            raise

    async def get(self, report_id: UUID) -> PreparedReportRecord | None:
        document = await self._collection.find_one({"_id": str(report_id)})
        return self._from_document(document) if document else None

    @staticmethod
    def _to_document(report: PreparedReportRecord) -> dict[str, Any]:
        return {
            "_id": str(report.report_id),
            "requested_by": str(report.requested_by),
            "recipients": [
                {
                    "recipient_id": str(recipient.recipient_id),
                    "name": recipient.name,
                    "email": recipient.email,
                }
                for recipient in report.recipients
            ],
            "subject": report.subject,
            "body": report.body,
            "attachments": [
                {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "content_base64": base64.b64encode(attachment.content).decode("ascii"),
                    "sha256": attachment.sha256,
                }
                for attachment in report.attachments
            ],
            "source_payload_hash": report.source_payload_hash,
            "created_at": report.created_at,
            "expires_at": report.expires_at,
            "state": report.state.value,
        }

    @staticmethod
    def _from_document(document: dict[str, Any]) -> PreparedReportRecord:
        return PreparedReportRecord(
            report_id=UUID(str(document["_id"])),
            requested_by=UUID(str(document["requested_by"])),
            recipients=tuple(
                ResolvedRecipient(
                    recipient_id=UUID(str(recipient["recipient_id"])),
                    name=recipient["name"],
                    email=recipient["email"],
                )
                for recipient in document["recipients"]
            ),
            subject=document["subject"],
            body=document["body"],
            attachments=tuple(
                ReportAttachmentRecord(
                    filename=attachment["filename"],
                    content_type=attachment["content_type"],
                    content=base64.b64decode(attachment["content_base64"], validate=True),
                    sha256=attachment["sha256"],
                )
                for attachment in document.get("attachments", ())
            ),
            source_payload_hash=document["source_payload_hash"],
            created_at=_as_datetime(document["created_at"]),
            expires_at=_as_datetime(document["expires_at"]),
            state=ReportState(document.get("state", ReportState.PREPARED.value)),
        )


def _as_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("campo de data inválido no documento Mongo")
    return value
