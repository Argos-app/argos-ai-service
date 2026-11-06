"""Adapter SMTP para envio pelo endereço no-reply do Argos."""

import asyncio
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape

from argos_mcp_email.domain import ReportAttachmentRecord
from argos_mcp_email.ports.mail_provider import ProviderSendResult
from packages.contracts import RecipientRef


class SmtpMailProvider:
    """Implementa ``MailProvider`` usando SMTP.

    A fábrica de conexão é injetável para testes e para substituir SMTP por um
    gateway HTTP no futuro.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._smtp_factory = smtp_factory

    async def send(
        self,
        *,
        sender: str,
        recipients: tuple[RecipientRef, ...],
        subject: str,
        body: str,
        attachments: tuple[ReportAttachmentRecord, ...] = (),
    ) -> ProviderSendResult:
        message_id = make_msgid()
        try:
            refused_recipients = await asyncio.to_thread(
                self._send_sync,
                sender,
                recipients,
                subject,
                body,
                attachments,
                message_id,
            )
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError) as exc:
            return ProviderSendResult(
                accepted=False,
                error_code=type(exc).__name__,
                delivery_unknown=True,
            )
        except smtplib.SMTPException as exc:
            return ProviderSendResult(
                accepted=False,
                error_code=type(exc).__name__,
            )
        refused_recipient_count = len(refused_recipients or {})
        return ProviderSendResult(
            accepted=True,
            provider_message_id=message_id,
            error_code=("partial_recipient_refusal" if refused_recipient_count else None),
            refused_recipient_count=refused_recipient_count,
        )

    def _send_sync(
        self,
        sender: str,
        recipients: tuple[RecipientRef, ...],
        subject: str,
        body: str,
        attachments: tuple[ReportAttachmentRecord, ...],
        message_id: str,
    ) -> dict[str, tuple[int, bytes]]:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipient.email for recipient in recipients)
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.set_content(body)
        message.add_alternative(
            '<!doctype html><html lang="pt-BR"><body style="margin:0;background:#edf2ee;'
            'font-family:Arial,sans-serif;color:#24332b;">'
            '<table role="presentation" style="width:100%;border-collapse:collapse;">'
            '<tr><td style="padding:36px 16px;">'
            '<table role="presentation" style="width:100%;max-width:640px;margin:auto;'
            'border-collapse:collapse;background:#ffffff;border:1px solid #dce7df;">'
            '<tr><td style="background:#234b39;padding:28px 32px;color:#ffffff;">'
            '<span style="font-size:13px;font-weight:bold;letter-spacing:2px;">ARGOS</span>'
            '<p style="font-size:11px;letter-spacing:1px;color:#cee0d3;margin:16px 0 6px;">'
            "RELATÓRIO DE LOTE</p>"
            f'<h1 style="font-size:22px;line-height:1.3;margin:0;">{escape(subject)}</h1>'
            '</td></tr><tr><td style="padding:28px 32px;">'
            '<p style="margin:0 0 20px;color:#5d7065;line-height:1.5;">'
            "Confira as informações do relatório abaixo.</p>"
            + (
                '<p style="margin:0 0 22px;padding:12px 16px;background:#edf5ef;'
                'border-left:3px solid #4f8061;color:#234b39;font-size:13px;">'
                "O relatório também está disponível no anexo deste e-mail.</p>"
                if attachments
                else ""
            )
            + '<div style="font-size:14px;line-height:1.7;white-space:pre-wrap;'
            f'overflow-wrap:anywhere;">{escape(body)}</div>'
            '</td></tr><tr><td style="border-top:1px solid #e4eae6;padding:20px 32px;'
            'font-size:12px;color:#718078;">Argos · Relatório de lote</td></tr>'
            "</table></td></tr></table></body></html>",
            subtype="html",
        )
        for attachment in attachments:
            maintype, subtype = attachment.content_type.split("/", maxsplit=1)
            message.add_attachment(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
                cte="base64",
            )

        with self._smtp_factory(self._host, self._port, timeout=30) as smtp:
            if self._use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self._username and self._password:
                smtp.login(self._username, self._password)
            return smtp.send_message(message)
