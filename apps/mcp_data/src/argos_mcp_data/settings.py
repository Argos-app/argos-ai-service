"""Configuração explícita do MCP de dados."""

import json
import os
import re
from dataclasses import dataclass, field

_MONGO_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


@dataclass(frozen=True, slots=True)
class Settings:
    sql_dsn: str | None = None
    mongo_uri: str | None = None
    mongo_database: str | None = "argos_ai"
    mongo_cattle_collection: str = "cattle_documents"
    mongo_relational_id_format: str | None = None
    mongo_medicine_collection: str = "medicine_documents"
    mongo_medicine_id_format: str | None = None
    internal_service_token: str | None = None
    mcp_auth_issuer_url: str | None = None
    mcp_resource_server_url: str | None = None
    delegation_issuer: str | None = None
    trusted_delegation_keys: tuple[tuple[str, str], ...] = ()
    report_signing_key_id: str | None = None
    report_signing_private_key: str | None = field(default=None, repr=False)
    report_envelope_ttl_seconds: int = 300
    delegation_max_ttl_seconds: int = 300
    delegation_clock_skew_seconds: int = 30
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        def read_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} deve ser um inteiro") from exc

        port = read_int("MCP_DATA_PORT", 8000)
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
            mongo_database=os.getenv("MCP_DATA_MONGO_DATABASE", "argos_ai"),
            mongo_relational_id_format=os.getenv("MCP_DATA_MONGO_RELATIONAL_ID_FORMAT"),
            mongo_medicine_id_format=os.getenv("MCP_DATA_MONGO_MEDICINE_ID_FORMAT"),
            internal_service_token=os.getenv("MCP_DATA_INTERNAL_SERVICE_TOKEN"),
            mcp_auth_issuer_url=os.getenv("MCP_DATA_AUTH_ISSUER_URL"),
            mcp_resource_server_url=os.getenv("MCP_DATA_RESOURCE_SERVER_URL"),
            delegation_issuer=os.getenv("MCP_DATA_DELEGATION_ISSUER"),
            trusted_delegation_keys=tuple(delegation_keys.items()),
            report_signing_key_id=os.getenv("MCP_DATA_REPORT_SIGNING_KEY_ID"),
            report_signing_private_key=os.getenv("MCP_DATA_REPORT_SIGNING_PRIVATE_KEY"),
            report_envelope_ttl_seconds=read_int("MCP_DATA_REPORT_ENVELOPE_TTL_SECONDS", 300),
            delegation_max_ttl_seconds=read_int("MCP_DATA_DELEGATION_MAX_TTL", 300),
            delegation_clock_skew_seconds=read_int("MCP_DATA_DELEGATION_CLOCK_SKEW", 30),
            mcp_host=os.getenv("MCP_DATA_HOST", "0.0.0.0"),
            mcp_port=port,
        )

    def validate_runtime(self) -> None:
        required = {
            "MCP_DATA_SQL_DSN": self.sql_dsn,
            "MCP_DATA_MONGO_URI": self.mongo_uri,
            "MCP_DATA_MONGO_DATABASE": self.mongo_database,
            "MCP_DATA_MONGO_RELATIONAL_ID_FORMAT": self.mongo_relational_id_format,
            "MCP_DATA_MONGO_MEDICINE_ID_FORMAT": self.mongo_medicine_id_format,
            "MCP_DATA_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "MCP_DATA_AUTH_ISSUER_URL": self.mcp_auth_issuer_url,
            "MCP_DATA_RESOURCE_SERVER_URL": self.mcp_resource_server_url,
            "MCP_DATA_DELEGATION_ISSUER": self.delegation_issuer,
        }
        missing = [name for name, value in required.items() if not value]
        if not self.trusted_delegation_keys:
            missing.append("MCP_DATA_TRUSTED_DELEGATION_KEYS")
        if not self.report_signing_key_id:
            missing.append("MCP_DATA_REPORT_SIGNING_KEY_ID")
        if not self.report_signing_private_key:
            missing.append("MCP_DATA_REPORT_SIGNING_PRIVATE_KEY")
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
        if self.mongo_relational_id_format not in {"string", "uuid_standard"}:
            raise ValueError(
                "MCP_DATA_MONGO_RELATIONAL_ID_FORMAT deve ser 'string' ou 'uuid_standard'"
            )
        if self.mongo_medicine_id_format not in {"string", "uuid_standard"}:
            raise ValueError(
                "MCP_DATA_MONGO_MEDICINE_ID_FORMAT deve ser 'string' ou 'uuid_standard'"
            )
        collection_names = (self.mongo_cattle_collection, self.mongo_medicine_collection)
        if any(
            not _MONGO_COLLECTION_NAME.fullmatch(name or "") or name.startswith("system.")
            for name in collection_names
        ):
            raise ValueError("nome de collection Mongo inválido")
        if len(set(collection_names)) != len(collection_names):
            raise ValueError("collections Mongo do MCP de dados devem ter nomes distintos")
        if not 1 <= self.delegation_max_ttl_seconds <= 300:
            raise ValueError("MCP_DATA_DELEGATION_MAX_TTL deve estar entre 1 e 300 segundos")
        if not 0 <= self.delegation_clock_skew_seconds <= 60:
            raise ValueError("MCP_DATA_DELEGATION_CLOCK_SKEW deve estar entre 0 e 60 segundos")
        if not 1 <= self.report_envelope_ttl_seconds <= 300:
            raise ValueError("MCP_DATA_REPORT_ENVELOPE_TTL_SECONDS deve estar entre 1 e 300")
