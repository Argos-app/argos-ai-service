"""Adapters de infraestrutura do MCP de dados."""

from argos_mcp_data.infrastructure.cattle_aggregate_repository import (
    PostgresCattleAggregateRepository,
)
from argos_mcp_data.infrastructure.cattle_document_repository import MongoCattleDocumentRepository
from argos_mcp_data.infrastructure.cattle_repository import PostgresCattleRepository
from argos_mcp_data.infrastructure.medicine_document_repository import (
    MongoMedicineDocumentRepository,
)
from argos_mcp_data.infrastructure.medicine_repository import PostgresMedicineRepository
from argos_mcp_data.infrastructure.mongo import ReadOnlyMongoReader
from argos_mcp_data.infrastructure.sql import AsyncpgSqlReader

__all__ = [
    "AsyncpgSqlReader",
    "PostgresCattleRepository",
    "PostgresCattleAggregateRepository",
    "MongoCattleDocumentRepository",
    "ReadOnlyMongoReader",
    "MongoMedicineDocumentRepository",
    "PostgresMedicineRepository",
]
