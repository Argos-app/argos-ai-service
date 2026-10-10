"""Configuração explícita do serviço A2A, seguindo o bootstrap dos MCPs."""

import json
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
    delegation_issuer: str | None = None
    trusted_delegation_keys: tuple[tuple[str, str], ...] = ()
    delegation_max_ttl_seconds: int = 300
    delegation_clock_skew_seconds: int = 30
    qdrant_timeout_seconds: float = 5
    a2a_host: str = "0.0.0.0"
    a2a_port: int = 8000
    a2a_public_url: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        raw_keys = os.getenv("A2A_TRUSTED_DELEGATION_KEYS", "{}")
        try:
            delegation_keys = json.loads(raw_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("A2A_TRUSTED_DELEGATION_KEYS deve ser JSON") from exc
        if not isinstance(delegation_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in delegation_keys.items()
        ):
            raise ValueError("A2A_TRUSTED_DELEGATION_KEYS deve mapear key ID para chave Base64")

        def read_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} deve ser um inteiro") from exc

        port = read_int("A2A_PORT", 8000)
        max_ttl = read_int("A2A_DELEGATION_MAX_TTL", 300)
        clock_skew = read_int("A2A_DELEGATION_CLOCK_SKEW", 30)
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
            delegation_issuer=os.getenv("A2A_DELEGATION_ISSUER"),
            trusted_delegation_keys=tuple(delegation_keys.items()),
            delegation_max_ttl_seconds=max_ttl,
            delegation_clock_skew_seconds=clock_skew,
            qdrant_timeout_seconds=timeout,
            a2a_host=os.getenv("A2A_HOST", "0.0.0.0"),
            a2a_port=port,
            a2a_public_url=os.getenv("A2A_PUBLIC_URL"),
        )

    def validate_runtime(self) -> None:
        required = {
            "A2A_QDRANT_URL": self.qdrant_url,
            "A2A_QDRANT_READ_API_KEY": self.qdrant_read_api_key,
            "A2A_QDRANT_COLLECTION": self.qdrant_collection,
            "A2A_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "A2A_RESOURCE_SERVER_URL": self.resource_server_url,
            "A2A_DELEGATION_ISSUER": self.delegation_issuer,
        }
        missing = [name for name, value in required.items() if not value or not value.strip()]
        if not self.trusted_delegation_keys:
            missing.append("A2A_TRUSTED_DELEGATION_KEYS")
        if missing:
            raise ValueError(f"configuração obrigatória ausente: {', '.join(missing)}")
        if not 1 <= self.a2a_port <= 65535:
            raise ValueError("A2A_PORT deve estar entre 1 e 65535")
        if not 1 <= self.delegation_max_ttl_seconds <= 300:
            raise ValueError("A2A_DELEGATION_MAX_TTL deve estar entre 1 e 300 segundos")
        if not 0 <= self.delegation_clock_skew_seconds <= 60:
            raise ValueError("A2A_DELEGATION_CLOCK_SKEW deve estar entre 0 e 60 segundos")
        if not self.a2a_host.strip():
            raise ValueError("A2A_HOST não pode ser vazio")
        if self.a2a_public_url is not None:
            try:
                parsed_public_url = urlsplit(self.a2a_public_url)
                valid_public_url = (
                    parsed_public_url.scheme in {"http", "https"}
                    and bool(parsed_public_url.hostname)
                    and parsed_public_url.username is None
                    and parsed_public_url.password is None
                    and parsed_public_url.path.rstrip("/").endswith("/a2a")
                    and not parsed_public_url.query
                    and not parsed_public_url.fragment
                    and (parsed_public_url.port is None or 1 <= parsed_public_url.port <= 65535)
                )
            except ValueError:
                valid_public_url = False
            if not valid_public_url:
                raise ValueError("A2A_PUBLIC_URL deve ser URL HTTP(S) completa terminando em /a2a")
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
