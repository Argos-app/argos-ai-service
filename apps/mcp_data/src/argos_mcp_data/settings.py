"""Configuração explícita do MCP de dados."""

import json
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
    delegation_issuer: str | None = None
    trusted_delegation_keys: tuple[tuple[str, str], ...] = ()
    delegation_max_ttl_seconds: int = 300
    delegation_clock_skew_seconds: int = 30
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
        raw_keys = os.getenv("MCP_DATA_TRUSTED_DELEGATION_KEYS", "{}")
        try:
            delegation_keys = json.loads(raw_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_DATA_TRUSTED_DELEGATION_KEYS deve ser JSON") from exc
        if not isinstance(delegation_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in delegation_keys.items()
        ):
            raise ValueError(
                "MCP_DATA_TRUSTED_DELEGATION_KEYS deve mapear key ID para chave Base64"
            )
        return cls(
            sql_dsn=os.getenv("MCP_DATA_SQL_DSN"),
            mongo_uri=os.getenv("MCP_DATA_MONGO_URI"),
            mongo_database=os.getenv("MCP_DATA_MONGO_DATABASE"),
            internal_service_token=os.getenv("MCP_DATA_INTERNAL_SERVICE_TOKEN"),
            mcp_auth_issuer_url=os.getenv("MCP_DATA_AUTH_ISSUER_URL"),
            mcp_resource_server_url=os.getenv("MCP_DATA_RESOURCE_SERVER_URL"),
            delegation_issuer=os.getenv("MCP_DATA_DELEGATION_ISSUER"),
            trusted_delegation_keys=tuple(delegation_keys.items()),
            delegation_max_ttl_seconds=int(os.getenv("MCP_DATA_DELEGATION_MAX_TTL", "300")),
            delegation_clock_skew_seconds=int(os.getenv("MCP_DATA_DELEGATION_CLOCK_SKEW", "30")),
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
            "MCP_DATA_DELEGATION_ISSUER": self.delegation_issuer,
        }
        missing = [name for name, value in required.items() if not value]
        if not self.trusted_delegation_keys:
            missing.append("MCP_DATA_TRUSTED_DELEGATION_KEYS")
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
        if not 1 <= self.delegation_max_ttl_seconds <= 300:
            raise ValueError("MCP_DATA_DELEGATION_MAX_TTL deve estar entre 1 e 300 segundos")
        if not 0 <= self.delegation_clock_skew_seconds <= 60:
            raise ValueError("MCP_DATA_DELEGATION_CLOCK_SKEW deve estar entre 0 e 60 segundos")
