"""Configuração do serviço MCP de e-mail."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str = "mcp_email"
    mongo_database: str = "argos_email"
    mongo_reports_collection: str = "prepared_reports"
    internal_service_token: str | None = None
    no_reply_address: str = "no-reply@argos.local"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            service_name=os.getenv("MCP_EMAIL_SERVICE_NAME", "mcp_email"),
            mongo_database=os.getenv("MCP_EMAIL_MONGO_DATABASE", "argos_email"),
            mongo_reports_collection=os.getenv("MCP_EMAIL_REPORTS_COLLECTION", "prepared_reports"),
            internal_service_token=os.getenv("MCP_EMAIL_INTERNAL_SERVICE_TOKEN"),
            no_reply_address=os.getenv("MCP_EMAIL_NO_REPLY_ADDRESS", "no-reply@argos.local"),
            smtp_host=os.getenv("MCP_EMAIL_SMTP_HOST"),
            smtp_port=int(os.getenv("MCP_EMAIL_SMTP_PORT", "587")),
            smtp_username=os.getenv("MCP_EMAIL_SMTP_USERNAME"),
            smtp_password=os.getenv("MCP_EMAIL_SMTP_PASSWORD"),
            smtp_use_tls=os.getenv("MCP_EMAIL_SMTP_USE_TLS", "true").lower() == "true",
        )
