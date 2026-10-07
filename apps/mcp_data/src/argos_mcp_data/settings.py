"""Configuração explícita do MCP de dados."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    sql_dsn: str | None = None
    mongo_uri: str | None = None
    mongo_database: str | None = None
    internal_service_token: str | None = None
    mcp_auth_issuer_url: str | None = None
    mcp_resource_server_url: str | None = None
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        try:
            port = int(os.getenv("MCP_DATA_PORT", "8000"))
        except ValueError as exc:
            raise ValueError("MCP_DATA_PORT deve ser um inteiro") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MCP_DATA_PORT deve estar entre 1 e 65535")
        return cls(
            sql_dsn=os.getenv("MCP_DATA_SQL_DSN"),
            mongo_uri=os.getenv("MCP_DATA_MONGO_URI"),
            mongo_database=os.getenv("MCP_DATA_MONGO_DATABASE"),
            internal_service_token=os.getenv("MCP_DATA_INTERNAL_SERVICE_TOKEN"),
            mcp_auth_issuer_url=os.getenv("MCP_DATA_AUTH_ISSUER_URL"),
            mcp_resource_server_url=os.getenv("MCP_DATA_RESOURCE_SERVER_URL"),
            mcp_host=os.getenv("MCP_DATA_HOST", "0.0.0.0"),
            mcp_port=port,
        )

    def validate_runtime(self) -> None:
        required = {
            "MCP_DATA_SQL_DSN": self.sql_dsn,
            "MCP_DATA_MONGO_URI": self.mongo_uri,
            "MCP_DATA_MONGO_DATABASE": self.mongo_database,
            "MCP_DATA_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "MCP_DATA_AUTH_ISSUER_URL": self.mcp_auth_issuer_url,
            "MCP_DATA_RESOURCE_SERVER_URL": self.mcp_resource_server_url,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
