"""Configuração externa do serviço principal."""

import os
from dataclasses import dataclass, field

from app.session_config import SESSION_SUMMARY_INPUT_MAX_TOKENS


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str | None = None
    mongo_database: str = "argos_ai"
    mongo_server_selection_timeout_ms: int = 5_000
    mongo_write_timeout_ms: int = 5_000
    session_summary_input_max_tokens: int = SESSION_SUMMARY_INPUT_MAX_TOKENS
    mcp_data_url: str | None = None
    mcp_data_token: str | None = field(default=None, repr=False)
    mcp_prediction_url: str | None = None
    mcp_prediction_token: str | None = field(default=None, repr=False)
    mcp_email_url: str | None = None
    mcp_email_token: str | None = field(default=None, repr=False)
    mcp_timeout_seconds: float = 10.0
    mcp_response_max_bytes: int = 1_000_000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mongo_uri=os.getenv("MAIN_API_MONGO_URI") or None,
            mongo_database=os.getenv("MAIN_API_MONGO_DATABASE", "argos_ai"),
            mongo_server_selection_timeout_ms=int(
                os.getenv("MAIN_API_MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")
            ),
            mongo_write_timeout_ms=int(os.getenv("MAIN_API_MONGO_WRITE_TIMEOUT_MS", "5000")),
            session_summary_input_max_tokens=int(
                os.getenv(
                    "MAIN_API_SESSION_SUMMARY_INPUT_MAX_TOKENS",
                    str(SESSION_SUMMARY_INPUT_MAX_TOKENS),
                )
            ),
            mcp_data_url=os.getenv("MAIN_API_MCP_DATA_URL") or None,
            mcp_data_token=os.getenv("MAIN_API_MCP_DATA_TOKEN") or None,
            mcp_prediction_url=os.getenv("MAIN_API_MCP_PREDICTION_URL") or None,
            mcp_prediction_token=os.getenv("MAIN_API_MCP_PREDICTION_TOKEN") or None,
            mcp_email_url=os.getenv("MAIN_API_MCP_EMAIL_URL") or None,
            mcp_email_token=os.getenv("MAIN_API_MCP_EMAIL_TOKEN") or None,
            mcp_timeout_seconds=float(os.getenv("MAIN_API_MCP_TIMEOUT_SECONDS", "10")),
            mcp_response_max_bytes=int(os.getenv("MAIN_API_MCP_RESPONSE_MAX_BYTES", "1000000")),
        )

    def __post_init__(self) -> None:
        if not self.mongo_database.strip():
            raise ValueError("MAIN_API_MONGO_DATABASE não pode estar vazio")
        if (
            self.mongo_server_selection_timeout_ms < 1
            or self.mongo_write_timeout_ms < 1
            or self.session_summary_input_max_tokens < 1
            or self.mcp_timeout_seconds <= 0
            or self.mcp_response_max_bytes < 1
        ):
            raise ValueError("limites e timeouts devem ser positivos")
