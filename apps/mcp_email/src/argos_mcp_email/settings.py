"""Configuração do serviço MCP de e-mail."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str = "mcp_email"
    mongo_database: str = "argos_email"
    mongo_reports_collection: str = "prepared_reports"
    internal_service_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            service_name=os.getenv("MCP_EMAIL_SERVICE_NAME", "mcp_email"),
            mongo_database=os.getenv("MCP_EMAIL_MONGO_DATABASE", "argos_email"),
            mongo_reports_collection=os.getenv("MCP_EMAIL_REPORTS_COLLECTION", "prepared_reports"),
            internal_service_token=os.getenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN"),
        )
