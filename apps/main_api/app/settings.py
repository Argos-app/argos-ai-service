"""Configuração externa do serviço principal."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str | None = None
    mongo_database: str = "argos_ai"
    mongo_server_selection_timeout_ms: int = 5_000
    mongo_write_timeout_ms: int = 5_000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mongo_uri=os.getenv("MAIN_API_MONGO_URI") or None,
            mongo_database=os.getenv("MAIN_API_MONGO_DATABASE", "argos_ai"),
            mongo_server_selection_timeout_ms=int(
                os.getenv("MAIN_API_MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")
            ),
            mongo_write_timeout_ms=int(os.getenv("MAIN_API_MONGO_WRITE_TIMEOUT_MS", "5000")),
        )

    def __post_init__(self) -> None:
        if not self.mongo_database.strip():
            raise ValueError("MAIN_API_MONGO_DATABASE não pode estar vazio")
        if self.mongo_server_selection_timeout_ms < 1 or self.mongo_write_timeout_ms < 1:
            raise ValueError("timeouts do Mongo devem ser positivos")
