"""Portas para dependências externas do MCP de predição."""

from .consumption_repository import ConsumptionReadRepository
from .coverage_repository import CoverageReadRepository
from .sql_reader import SqlReader

__all__ = ["ConsumptionReadRepository", "CoverageReadRepository", "SqlReader"]
