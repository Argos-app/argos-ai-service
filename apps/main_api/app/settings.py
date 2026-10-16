"""Configuração externa do serviço principal."""

import os
from dataclasses import dataclass

from app.session_config import SESSION_SUMMARY_INPUT_MAX_TOKENS


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str | None = None
    mongo_database: str = "argos_ai"
    mongo_server_selection_timeout_ms: int = 5_000
    mongo_write_timeout_ms: int = 5_000
    session_summary_input_max_tokens: int = SESSION_SUMMARY_INPUT_MAX_TOKENS

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
        )

    def __post_init__(self) -> None:
        if not self.mongo_database.strip():
            raise ValueError("MAIN_API_MONGO_DATABASE não pode estar vazio")
        if (
            self.mongo_server_selection_timeout_ms < 1
            or self.mongo_write_timeout_ms < 1
            or self.session_summary_input_max_tokens < 1
        ):
            raise ValueError("limites e timeouts devem ser positivos")
