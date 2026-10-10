"""Configuração explícita do serviço A2A, seguindo o bootstrap dos MCPs."""

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Settings:
    qdrant_url: str | None = None
    qdrant_read_api_key: str | None = field(default=None, repr=False)
    qdrant_write_api_key: str | None = field(default=None, repr=False)
    qdrant_collection: str | None = None
    qdrant_vector_size: int | None = None
    qdrant_distance: str = "Cosine"
    document_source_root: Path | None = None
    document_catalog_name: str = "catalog.json"
    manifest_path: Path | None = None
    internal_service_token: str | None = field(default=None, repr=False)
    resource_server_url: str | None = None
    delegation_issuer: str | None = None
    trusted_delegation_keys: tuple[tuple[str, str], ...] = ()
    delegation_max_ttl_seconds: int = 300
    delegation_clock_skew_seconds: int = 30
    qdrant_timeout_seconds: float = 5
    a2a_host: str = "0.0.0.0"
    a2a_port: int = 8000
    a2a_endpoint_url: str | None = None

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
            qdrant_write_api_key=os.getenv("A2A_QDRANT_WRITE_API_KEY"),
            qdrant_collection=os.getenv("A2A_QDRANT_COLLECTION"),
            qdrant_vector_size=read_int("A2A_QDRANT_VECTOR_SIZE", 0) or None,
            qdrant_distance=os.getenv("A2A_QDRANT_DISTANCE", "Cosine"),
            document_source_root=(
                Path(os.environ["A2A_DOCUMENT_SOURCE_ROOT"])
                if os.getenv("A2A_DOCUMENT_SOURCE_ROOT")
                else None
            ),
            document_catalog_name=os.getenv("A2A_DOCUMENT_CATALOG_NAME", "catalog.json"),
            manifest_path=(
                Path(os.environ["A2A_MANIFEST_PATH"]) if os.getenv("A2A_MANIFEST_PATH") else None
            ),
            internal_service_token=os.getenv("A2A_INTERNAL_SERVICE_TOKEN"),
            resource_server_url=os.getenv("A2A_RESOURCE_SERVER_URL"),
            delegation_issuer=os.getenv("A2A_DELEGATION_ISSUER"),
            trusted_delegation_keys=tuple(delegation_keys.items()),
            delegation_max_ttl_seconds=max_ttl,
            delegation_clock_skew_seconds=clock_skew,
            qdrant_timeout_seconds=timeout,
            a2a_host=os.getenv("A2A_HOST", "0.0.0.0"),
            a2a_port=port,
            a2a_endpoint_url=os.getenv("A2A_ENDPOINT_URL"),
        )

    def validate_runtime(self) -> None:
        required = {
            "A2A_QDRANT_URL": self.qdrant_url,
            "A2A_QDRANT_READ_API_KEY": self.qdrant_read_api_key,
            "A2A_QDRANT_WRITE_API_KEY": self.qdrant_write_api_key,
            "A2A_QDRANT_COLLECTION": self.qdrant_collection,
            "A2A_QDRANT_VECTOR_SIZE": self.qdrant_vector_size,
            "A2A_DOCUMENT_SOURCE_ROOT": self.document_source_root,
            "A2A_MANIFEST_PATH": self.manifest_path,
            "A2A_INTERNAL_SERVICE_TOKEN": self.internal_service_token,
            "A2A_RESOURCE_SERVER_URL": self.resource_server_url,
            "A2A_DELEGATION_ISSUER": self.delegation_issuer,
            "A2A_ENDPOINT_URL": self.a2a_endpoint_url,
        }
        missing = [
            name
            for name, value in required.items()
            if value is None or isinstance(value, str) and not value.strip()
        ]
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
        if self.qdrant_read_api_key == self.qdrant_write_api_key:
            raise ValueError("credenciais Qdrant de leitura e escrita devem ser distintas")
        if (
            isinstance(self.qdrant_vector_size, bool)
            or not isinstance(self.qdrant_vector_size, int)
            or not 1 <= self.qdrant_vector_size <= 65_536
        ):
            raise ValueError("A2A_QDRANT_VECTOR_SIZE deve estar entre 1 e 65536")
        if self.qdrant_distance not in {"Cosine", "Euclid", "Dot", "Manhattan"}:
            raise ValueError("A2A_QDRANT_DISTANCE deve ser Cosine, Euclid, Dot ou Manhattan")
        if not self.document_catalog_name.strip():
            raise ValueError("A2A_DOCUMENT_CATALOG_NAME não pode ser vazio")
        if self.document_source_root is not None and not self.document_source_root.is_absolute():
            raise ValueError("A2A_DOCUMENT_SOURCE_ROOT deve ser um caminho absoluto")
        if self.manifest_path is not None and not self.manifest_path.is_absolute():
            raise ValueError("A2A_MANIFEST_PATH deve ser um caminho absoluto")
        if self.a2a_endpoint_url is not None:
            try:
                parsed_endpoint_url = urlsplit(self.a2a_endpoint_url)
                valid_endpoint_url = (
                    parsed_endpoint_url.scheme in {"http", "https"}
                    and bool(parsed_endpoint_url.hostname)
                    and parsed_endpoint_url.username is None
                    and parsed_endpoint_url.password is None
                    and parsed_endpoint_url.path.rstrip("/").endswith("/a2a")
                    and not parsed_endpoint_url.query
                    and not parsed_endpoint_url.fragment
                    and (parsed_endpoint_url.port is None or 1 <= parsed_endpoint_url.port <= 65535)
                )
            except ValueError:
                valid_endpoint_url = False
            if not valid_endpoint_url:
                raise ValueError(
                    "A2A_ENDPOINT_URL deve ser URL HTTP(S) completa terminando em /a2a"
                )
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
