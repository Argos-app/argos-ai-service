import pytest
from argos_mcp_email.server import ServiceAuthenticationError, ServiceAuthenticator
from argos_mcp_email.settings import Settings


def test_authenticator_requires_expected_bearer_token() -> None:
    authenticator = ServiceAuthenticator("secret")
    authenticator.verify("Bearer secret")

    with pytest.raises(ServiceAuthenticationError):
        authenticator.verify("Bearer wrong")


def test_settings_reads_service_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN", "secret")

    assert Settings.from_env().internal_service_token == "secret"
