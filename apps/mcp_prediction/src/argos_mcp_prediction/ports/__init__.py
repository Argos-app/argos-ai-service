"""Portas para dependências externas do MCP de predição."""

from .consumption_repository import ConsumptionReadRepository
from .sql_reader import SqlReader

__all__ = ["ConsumptionReadRepository", "SqlReader"]
