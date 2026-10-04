from uuid import uuid4

import pytest
from argos_mcp_email.adapters.provider import SmtpMailProvider

from packages.contracts import RecipientRef


class FakeSmtp:
    messages = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_smtp_provider_sends_from_configured_sender():
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
    )

    assert result.accepted is True
    assert FakeSmtp.messages[-1]["From"] == "no-reply@argos.com"
    assert FakeSmtp.messages[-1]["To"] == "luis@example.com"
