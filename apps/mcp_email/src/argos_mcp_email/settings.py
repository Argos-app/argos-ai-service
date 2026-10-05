"""Configuração do serviço MCP de e-mail."""

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str = "mcp_email"
    mongo_uri: str | None = None
    mongo_database: str = "argos_email"
    mongo_reports_collection: str = "prepared_reports"
    mongo_dispatch_collection: str = "email_dispatches"
    sql_dsn: str | None = None
    trusted_signature_keys: tuple[tuple[str, str], ...] = ()
    internal_service_token: str | None = None
    no_reply_address: str = "no-reply@argos.local"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        raw_keys = os.getenv("MCP_EMAIL_TRUSTED_SIGNATURE_KEYS", "{}")
        try:
            trusted_keys = json.loads(raw_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_EMAIL_TRUSTED_SIGNATURE_KEYS deve ser JSON") from exc
        if not isinstance(trusted_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in trusted_keys.items()
        ):
            raise ValueError(
                "MCP_EMAIL_TRUSTED_SIGNATURE_KEYS deve mapear IDs para chaves Base64"
            )
        return cls(
            service_name=os.getenv("MCP_EMAIL_SERVICE_NAME", "mcp_email"),
            mongo_uri=os.getenv("MCP_EMAIL_MONGO_URI"),
            mongo_database=os.getenv("MCP_EMAIL_MONGO_DATABASE", "argos_email"),
            mongo_reports_collection=os.getenv("MCP_EMAIL_REPORTS_COLLECTION", "prepared_reports"),
            mongo_dispatch_collection=os.getenv(
                "MCP_EMAIL_DISPATCH_COLLECTION", "email_dispatches"
            ),
            sql_dsn=os.getenv("MCP_EMAIL_SQL_DSN"),
            trusted_signature_keys=tuple(trusted_keys.items()),
            internal_service_token=os.getenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN"),
            no_reply_address=os.getenv("MCP_EMAIL_NO_REPLY_ADDRESS", "no-reply@argos.local"),
            smtp_host=os.getenv("MCP_EMAIL_SMTP_HOST"),
            smtp_port=int(os.getenv("MCP_EMAIL_SMTP_PORT", "587")),
            smtp_username=os.getenv("MCP_EMAIL_SMTP_USERNAME"),
            smtp_password=os.getenv("MCP_EMAIL_SMTP_PASSWORD"),
            smtp_use_tls=os.getenv("MCP_EMAIL_SMTP_USE_TLS", "true").lower() == "true",
        )
