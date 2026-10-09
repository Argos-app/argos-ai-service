"""Infraestrutura do MCP de predição."""

from .sql import AsyncpgSqlReader

__all__ = ["AsyncpgSqlReader"]
