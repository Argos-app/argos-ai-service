from hashlib import sha256
from uuid import uuid4

import pytest
from argos_mcp_email.adapters.provider import SmtpMailProvider
from argos_mcp_email.domain import ReportAttachmentRecord

from packages.contracts import RecipientRef


class FakeSmtp:
    messages = []
    tls_contexts = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, *, context=None):
        self.tls_contexts.append(context)

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message):
        self.messages.append(message)
        return {}


@pytest.mark.asyncio
async def test_smtp_provider_sends_from_configured_sender():
    content = "Relatório sintético — lote HC-LOTE-001".encode()
    attachment = ReportAttachmentRecord(
        filename="relatorio-hc-lote-001.txt",
        content_type="text/plain",
        content=content,
        sha256=sha256(content).hexdigest(),
    )
    provider = SmtpMailProvider(
        host="smtp.example.com",
        port=587,
        username="user",
        password="password",
        smtp_factory=FakeSmtp,
    )

    result = await provider.send(
        sender="no-reply@argos.com",
        recipients=(RecipientRef(recipient_id=uuid4(), name="Luis", email="luis@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        attachments=(attachment,),
    )

    assert result.accepted is True
    assert FakeSmtp.messages[-1]["From"] == "no-reply@argos.com"
    assert FakeSmtp.messages[-1]["To"] == "luis@example.com"
    assert FakeSmtp.tls_contexts[-1].verify_mode == 2
    assert FakeSmtp.tls_contexts[-1].check_hostname is True
    sent_attachment = tuple(FakeSmtp.messages[-1].iter_attachments())[0]
    assert sent_attachment.get_filename() == attachment.filename
    assert sent_attachment.get_content_type() == attachment.content_type
    assert sent_attachment.get_payload(decode=True) == content
