"""Configuração independente da fundação do MCP de predição."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Settings:
    sql_dsn: str | None = field(default=None, repr=False)
    internal_service_token: str | None = field(default=None, repr=False)
    mcp_auth_issuer_url: str | None = None
    mcp_resource_server_url: str | None = None
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        def read_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} deve ser um inteiro") from exc

        return cls(
            sql_dsn=os.getenv("MCP_PREDICTION_SQL_DSN"),
            internal_service_token=os.getenv("MCP_PREDICTION_INTERNAL_SERVICE_TOKEN"),
            mcp_auth_issuer_url=os.getenv("MCP_PREDICTION_AUTH_ISSUER_URL"),
            mcp_resource_server_url=os.getenv("MCP_PREDICTION_RESOURCE_SERVER_URL"),
            mcp_host=os.getenv("MCP_PREDICTION_HOST", "0.0.0.0"),
            mcp_port=read_int("MCP_PREDICTION_PORT", 8000),
        )

    def validate_runtime(self) -> None:
        required = {
            "MCP_PREDICTION_SQL_DSN": self.sql_dsn,
            "MCP_PREDICTION_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "MCP_PREDICTION_AUTH_ISSUER_URL": self.mcp_auth_issuer_url,
            "MCP_PREDICTION_RESOURCE_SERVER_URL": self.mcp_resource_server_url,
        }
        missing = [name for name, value in required.items() if not value or not value.strip()]
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
        if not 1 <= self.mcp_port <= 65535:
            raise ValueError("MCP_PREDICTION_PORT deve estar entre 1 e 65535")
