"""Reutiliza o servidor isolado de e-mail como dependência externa de teste."""

pytest_plugins = ("apps.mcp_email.tests.integration.conftest",)
