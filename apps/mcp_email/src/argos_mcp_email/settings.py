"""Configuração do serviço MCP de e-mail."""

import json
import os
import re
from dataclasses import dataclass

_MONGO_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str = "mcp_email"
    mongo_uri: str | None = None
    mongo_database: str = "argos_ai"
    mongo_reports_collection: str = "ai_email_prepared_reports"
    mongo_dispatch_collection: str = "ai_email_dispatches"
    sql_dsn: str | None = None
    trusted_signature_keys: tuple[tuple[str, str], ...] = ()
    internal_service_token: str | None = None
    mcp_auth_issuer_url: str | None = None
    mcp_resource_server_url: str | None = None
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    no_reply_address: str = "no-reply@argos.local"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    def __post_init__(self) -> None:
        if not self.mongo_database.strip():
            raise ValueError("MCP_EMAIL_MONGO_DATABASE não pode estar vazio")
        names = (self.mongo_reports_collection, self.mongo_dispatch_collection)
        if any(
            not _MONGO_COLLECTION_NAME.fullmatch(name) or name.startswith("system.")
            for name in names
        ):
            raise ValueError("nome de collection Mongo inválido")
        if len(set(names)) != len(names):
            raise ValueError("collections Mongo do MCP de e-mail devem ter nomes distintos")

    @classmethod
    def from_env(cls) -> Settings:
        raw_keys = os.getenv("MCP_EMAIL_TRUSTED_SIGNATURE_KEYS", "{}")
        try:
            trusted_keys = json.loads(raw_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_EMAIL_TRUSTED_SIGNATURE_KEYS deve ser JSON") from exc
        if not isinstance(trusted_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in trusted_keys.items()
        ):
            raise ValueError("MCP_EMAIL_TRUSTED_SIGNATURE_KEYS deve mapear IDs para chaves Base64")
        return cls(
            service_name=os.getenv("MCP_EMAIL_SERVICE_NAME", "mcp_email"),
            mongo_uri=os.getenv("MCP_EMAIL_MONGO_URI"),
            mongo_database=os.getenv("MCP_EMAIL_MONGO_DATABASE", "argos_ai"),
            sql_dsn=os.getenv("MCP_EMAIL_SQL_DSN"),
            trusted_signature_keys=tuple(trusted_keys.items()),
            internal_service_token=os.getenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN"),
            mcp_auth_issuer_url=os.getenv("MCP_EMAIL_AUTH_ISSUER_URL"),
            mcp_resource_server_url=os.getenv("MCP_EMAIL_RESOURCE_SERVER_URL"),
            mcp_host=os.getenv("MCP_EMAIL_HOST", "0.0.0.0"),
            mcp_port=int(os.getenv("MCP_EMAIL_PORT", "8000")),
            no_reply_address=os.getenv("MCP_EMAIL_NO_REPLY_ADDRESS", "no-reply@argos.local"),
            smtp_host=os.getenv("MCP_EMAIL_SMTP_HOST"),
            smtp_port=int(os.getenv("MCP_EMAIL_SMTP_PORT", "587")),
            smtp_username=os.getenv("MCP_EMAIL_SMTP_USERNAME"),
            smtp_password=os.getenv("MCP_EMAIL_SMTP_PASSWORD"),
            smtp_use_tls=os.getenv("MCP_EMAIL_SMTP_USE_TLS", "true").lower() == "true",
        )
