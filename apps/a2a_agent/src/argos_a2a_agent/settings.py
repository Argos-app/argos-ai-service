"""Configuração explícita do serviço A2A, seguindo o bootstrap dos MCPs."""

import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Settings:
    qdrant_url: str | None = None
    qdrant_read_api_key: str | None = field(default=None, repr=False)
    qdrant_collection: str | None = None
    internal_service_token: str | None = field(default=None, repr=False)
    resource_server_url: str | None = None
    qdrant_timeout_seconds: float = 5
    a2a_host: str = "0.0.0.0"
    a2a_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        try:
            port = int(os.getenv("A2A_PORT", "8000"))
        except ValueError as exc:
            raise ValueError("A2A_PORT deve ser um inteiro") from exc
        try:
            timeout = float(os.getenv("A2A_QDRANT_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise ValueError("A2A_QDRANT_TIMEOUT_SECONDS deve ser um número") from exc
        return cls(
            qdrant_url=os.getenv("A2A_QDRANT_URL"),
            qdrant_read_api_key=os.getenv("A2A_QDRANT_READ_API_KEY"),
            qdrant_collection=os.getenv("A2A_QDRANT_COLLECTION"),
            internal_service_token=os.getenv("A2A_INTERNAL_SERVICE_TOKEN"),
            resource_server_url=os.getenv("A2A_RESOURCE_SERVER_URL"),
            qdrant_timeout_seconds=timeout,
            a2a_host=os.getenv("A2A_HOST", "0.0.0.0"),
            a2a_port=port,
        )

    def validate_runtime(self) -> None:
        required = {
            "A2A_QDRANT_URL": self.qdrant_url,
            "A2A_QDRANT_READ_API_KEY": self.qdrant_read_api_key,
            "A2A_QDRANT_COLLECTION": self.qdrant_collection,
            "A2A_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "A2A_RESOURCE_SERVER_URL": self.resource_server_url,
        }
        missing = [name for name, value in required.items() if not value or not value.strip()]
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
        if not 1 <= self.a2a_port <= 65535:
            raise ValueError("A2A_PORT deve estar entre 1 e 65535")
        if not self.a2a_host.strip():
            raise ValueError("A2A_HOST não pode ser vazio")
        if (
            not math.isfinite(self.qdrant_timeout_seconds)
            or not 0 < self.qdrant_timeout_seconds <= 30
        ):
            raise ValueError("A2A_QDRANT_TIMEOUT_SECONDS deve ser maior que 0 e até 30")
        for name, value in (
            ("A2A_QDRANT_URL", self.qdrant_url),
            ("A2A_RESOURCE_SERVER_URL", self.resource_server_url),
        ):
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and not parsed.query
                    and not parsed.fragment
                )
                valid = valid and (parsed.port is None or 1 <= parsed.port <= 65535)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{name} deve ser uma URL HTTP(S) sem credenciais ou query")
        if any(char.isspace() for char in self.internal_service_token):
            raise ValueError("A2A_INTERNAL_SERVICE_TOKEN não pode conter espaços")
