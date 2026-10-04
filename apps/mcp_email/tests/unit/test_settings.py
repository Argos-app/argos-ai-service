from argos_mcp_email.settings import Settings


def test_settings_reads_no_reply_and_smtp(monkeypatch):
    monkeypatch.setenv("MCP_EMAIL_NO_REPLY_ADDRESS", "no-reply@argos.com")
    monkeypatch.setenv("MCP_EMAIL_SMTP_HOST", "smtp.example.com")

    settings = Settings.from_env()

    assert settings.no_reply_address == "no-reply@argos.com"
    assert settings.smtp_host == "smtp.example.com"
