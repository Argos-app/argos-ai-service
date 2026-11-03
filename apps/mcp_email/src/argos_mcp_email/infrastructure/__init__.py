"""Integrações de infraestrutura usadas pela composição do serviço."""

from argos_mcp_email.infrastructure.sql import AsyncpgSqlExecutor

__all__ = ["AsyncpgSqlExecutor"]
