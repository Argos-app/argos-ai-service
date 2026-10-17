"""Caso de uso para validar e preparar um relatório de lote."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from argos_mcp_email.domain import (
    PreparedReportRecord,
    ReportAttachmentRecord,
    ResolvedRecipient,
)
from argos_mcp_email.ports.recipient_directory import RecipientDirectory
from argos_mcp_email.ports.report_renderer import ReportRenderer
from argos_mcp_email.ports.report_repository import ReportRepository
from argos_mcp_email.ports.source_verifier import SourceVerifier
from packages.contracts import (
    PreparedReport,
    PreparedReportStatus,
    PrepareReportRequest,
    RecipientResolutionStatus,
    ReportAttachment,
)


class PrepareReport:
    """Coordena as portas necessárias para preparar um relatório."""

    def __init__(
        self,
        *,
        source_verifier: SourceVerifier,
        recipient_directory: RecipientDirectory,
        renderer: ReportRenderer,
        report_repository: ReportRepository,
        preparation_ttl: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._source_verifier = source_verifier
        self._recipient_directory = recipient_directory
        self._renderer = renderer
        self._report_repository = report_repository
        self._preparation_ttl = preparation_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    async def execute(self, request: PrepareReportRequest) -> PreparedReport:
        source = await self._source_verifier.verify(request.report_source)
        resolutions = await self._recipient_directory.resolve(request.recipients)
        unresolved = tuple(
            resolution
            for resolution in resolutions
            if resolution.status != RecipientResolutionStatus.RESOLVED
        )
        now = self._clock()
        report_id = self._id_factory()
        expires_at = now + self._preparation_ttl

        if unresolved:
            return PreparedReport(
                report_id=report_id,
                status=PreparedReportStatus.NEEDS_RECIPIENTS,
                requested_by=request.requested_by,
                created_at=now,
                expires_at=expires_at,
                unresolved=unresolved,
            )

        recipients = tuple(
            resolution.matches[0]
            for resolution in resolutions
            if resolution.status == RecipientResolutionStatus.RESOLVED
        )
        rendered = self._renderer.render(
            report=source.report,
            recipients=recipients,
            title=request.report_title,
        )
        attachment_content = rendered.body.encode("utf-8")
        attachment = ReportAttachmentRecord(
            filename=f"relatorio-{report_id}.txt",
            content_type="text/plain",
            content=attachment_content,
            sha256=sha256(attachment_content).hexdigest(),
        )
        record = PreparedReportRecord(
            report_id=report_id,
            requested_by=request.requested_by,
            recipients=tuple(
                ResolvedRecipient(
                    recipient_id=recipient.recipient_id,
                    name=recipient.name,
                    email=recipient.email,
                )
                for recipient in recipients
            ),
            subject=rendered.subject,
            body=rendered.body,
            source_payload_hash=source.payload_hash,
            created_at=now,
            expires_at=expires_at,
            attachments=(attachment,),
        )
        await self._report_repository.save(record)

        return PreparedReport(
            report_id=record.report_id,
            status=PreparedReportStatus.PREPARED,
            requested_by=record.requested_by,
            recipients=recipients,
            subject=record.subject,
            body=record.body,
            attachments=(
                ReportAttachment(
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    sha256=attachment.sha256,
                    size_bytes=len(attachment.content),
                ),
            ),
            created_at=record.created_at,
            expires_at=record.expires_at,
        )
