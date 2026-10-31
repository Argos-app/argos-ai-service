"""Reutiliza o servidor isolado de e-mail como dependência externa de teste."""

from apps.mcp_email.tests.integration.conftest import mcp_harness

__all__ = ["mcp_harness"]
