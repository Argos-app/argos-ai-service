"""Configuração independente da fundação do MCP de predição."""

import json
import os
import re
from dataclasses import dataclass, field

from packages.contracts import DataOrigin

_COVERAGE_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


@dataclass(frozen=True, slots=True)
class Settings:
    sql_dsn: str | None = field(default=None, repr=False)
    internal_service_token: str | None = field(default=None, repr=False)
    mcp_auth_issuer_url: str | None = None
    mcp_resource_server_url: str | None = None
    delegation_issuer: str | None = None
    trusted_delegation_keys: tuple[tuple[str, str], ...] = ()
    delegation_max_ttl_seconds: int = 300
    delegation_clock_skew_seconds: int = 30
    sql_timeout_seconds: int = 5
    data_origin: DataOrigin = DataOrigin.UNKNOWN
    coverage_relation: str | None = None
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        def read_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} deve ser um inteiro") from exc

        raw_keys = os.getenv("MCP_PREDICTION_TRUSTED_DELEGATION_KEYS", "{}")
        try:
            delegation_keys = json.loads(raw_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_PREDICTION_TRUSTED_DELEGATION_KEYS deve ser JSON") from exc
        if not isinstance(delegation_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in delegation_keys.items()
        ):
            raise ValueError(
                "MCP_PREDICTION_TRUSTED_DELEGATION_KEYS deve mapear key ID para chave Base64"
            )
        try:
            data_origin = DataOrigin(os.getenv("MCP_PREDICTION_DATA_ORIGIN", "unknown"))
        except ValueError as exc:
            raise ValueError(
                "MCP_PREDICTION_DATA_ORIGIN deve ser recorded, synthetic ou unknown"
            ) from exc
        return cls(
            sql_dsn=os.getenv("MCP_PREDICTION_SQL_DSN"),
            internal_service_token=os.getenv("MCP_PREDICTION_INTERNAL_SERVICE_TOKEN"),
            mcp_auth_issuer_url=os.getenv("MCP_PREDICTION_AUTH_ISSUER_URL"),
            mcp_resource_server_url=os.getenv("MCP_PREDICTION_RESOURCE_SERVER_URL"),
            delegation_issuer=os.getenv("MCP_PREDICTION_DELEGATION_ISSUER"),
            trusted_delegation_keys=tuple(delegation_keys.items()),
            delegation_max_ttl_seconds=read_int("MCP_PREDICTION_DELEGATION_MAX_TTL", 300),
            delegation_clock_skew_seconds=read_int("MCP_PREDICTION_DELEGATION_CLOCK_SKEW", 30),
            sql_timeout_seconds=read_int("MCP_PREDICTION_SQL_TIMEOUT_SECONDS", 5),
            data_origin=data_origin,
            coverage_relation=os.getenv("MCP_PREDICTION_COVERAGE_RELATION") or None,
            mcp_host=os.getenv("MCP_PREDICTION_HOST", "0.0.0.0"),
            mcp_port=read_int("MCP_PREDICTION_PORT", 8000),
        )

    def validate_runtime(self) -> None:
        required = {
            "MCP_PREDICTION_DELEGATION_ISSUER": self.delegation_issuer,
            "MCP_PREDICTION_SQL_DSN": self.sql_dsn,
            "MCP_PREDICTION_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "MCP_PREDICTION_AUTH_ISSUER_URL": self.mcp_auth_issuer_url,
            "MCP_PREDICTION_RESOURCE_SERVER_URL": self.mcp_resource_server_url,
        }
        missing = [name for name, value in required.items() if not value or not value.strip()]
        if not self.trusted_delegation_keys:
            missing.append("MCP_PREDICTION_TRUSTED_DELEGATION_KEYS")
        if not isinstance(self.data_origin, DataOrigin):
            raise ValueError("MCP_PREDICTION_DATA_ORIGIN inválida")
        if self.coverage_relation is not None and not _COVERAGE_RELATION.fullmatch(
            self.coverage_relation
        ):
            raise ValueError("MCP_PREDICTION_COVERAGE_RELATION deve ser schema.view ou view")
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
        if not 1 <= self.mcp_port <= 65535:
            raise ValueError("MCP_PREDICTION_PORT deve estar entre 1 e 65535")
        if not 1 <= self.delegation_max_ttl_seconds <= 300:
            raise ValueError("MCP_PREDICTION_DELEGATION_MAX_TTL deve estar entre 1 e 300 segundos")
        if not 0 <= self.delegation_clock_skew_seconds <= 60:
            raise ValueError(
                "MCP_PREDICTION_DELEGATION_CLOCK_SKEW deve estar entre 0 e 60 segundos"
            )
        if not 1 <= self.sql_timeout_seconds <= 60:
            raise ValueError("MCP_PREDICTION_SQL_TIMEOUT_SECONDS deve estar entre 1 e 60")
