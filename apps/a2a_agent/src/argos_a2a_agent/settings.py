"""Configuração explícita do serviço A2A, seguindo o bootstrap dos MCPs."""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)


def _observability_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _LOGGER.warning("invalid observability setting; using default", extra={"setting": name})
        return default


def _environment_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    _LOGGER.warning("invalid boolean setting; using default", extra={"setting": name})
    return default


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
    embeddings_api_url: str | None = None
    embeddings_api_key: str | None = field(default=None, repr=False)
    embeddings_model: str | None = None
    embeddings_model_revision: str | None = None
    answer_api_url: str | None = None
    answer_api_key: str | None = field(default=None, repr=False)
    answer_model: str | None = None
    answer_thinking: str = "auto"
    permission_sql_dsn: str | None = field(default=None, repr=False)
    model_timeout_seconds: float = 40
    model_max_output_tokens: int = 4_096
    model_mode: str = "provider"
    a2a_host: str = "0.0.0.0"
    a2a_port: int = 8000
    a2a_endpoint_url: str | None = None
    observability_mongo_uri: str | None = field(default=None, repr=False)
    observability_mongo_database: str = "argos_ai"
    observability_collection: str | None = None
    observability_mongo_server_selection_timeout_ms: int = 5_000
    observability_mongo_write_timeout_ms: int = 5_000
    observability_queue_max_items: int = 2_048
    observability_queue_max_bytes: int = 8 * 1024 * 1024
    observability_document_max_bytes: int = 16 * 1024
    observability_batch_size: int = 100
    observability_batch_interval_ms: int = 250
    observability_max_attempts: int = 3
    observability_retry_base_delay_ms: int = 50
    observability_shutdown_timeout_ms: int = 2_000
    langsmith_api_key: str | None = field(default=None, repr=False)
    langsmith_project: str = "argos-ai-service"
    langsmith_endpoint: str | None = None
    langsmith_capture_content: bool = False
    langsmith_workspace_id: str | None = None

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
        try:
            model_timeout = float(os.getenv("A2A_MODEL_TIMEOUT_SECONDS", "40"))
        except ValueError as exc:
            raise ValueError("A2A_MODEL_TIMEOUT_SECONDS deve ser um número") from exc
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
            embeddings_api_url=os.getenv("A2A_EMBEDDINGS_API_URL"),
            embeddings_api_key=os.getenv("A2A_EMBEDDINGS_API_KEY"),
            embeddings_model=os.getenv("A2A_EMBEDDINGS_MODEL"),
            embeddings_model_revision=os.getenv("A2A_EMBEDDINGS_MODEL_REVISION"),
            answer_api_url=os.getenv("A2A_ANSWER_API_URL"),
            answer_api_key=os.getenv("A2A_ANSWER_API_KEY"),
            answer_model=os.getenv("A2A_ANSWER_MODEL"),
            answer_thinking=os.getenv("A2A_ANSWER_THINKING", "auto").strip().casefold(),
            permission_sql_dsn=os.getenv("A2A_PERMISSION_SQL_DSN"),
            model_timeout_seconds=model_timeout,
            model_max_output_tokens=read_int("A2A_MODEL_MAX_OUTPUT_TOKENS", 4_096),
            model_mode=os.getenv("A2A_MODEL_MODE", "provider").strip().casefold(),
            a2a_host=os.getenv("A2A_HOST", "0.0.0.0"),
            a2a_port=port,
            a2a_endpoint_url=os.getenv("A2A_ENDPOINT_URL"),
            observability_mongo_uri=os.getenv("A2A_OBSERVABILITY_MONGO_URI") or None,
            observability_mongo_database=os.getenv("A2A_OBSERVABILITY_MONGO_DATABASE", "argos_ai"),
            observability_collection=os.getenv("A2A_OBSERVABILITY_COLLECTION") or None,
            observability_mongo_server_selection_timeout_ms=_observability_int(
                "A2A_OBSERVABILITY_MONGO_SERVER_SELECTION_TIMEOUT_MS", 5_000
            ),
            observability_mongo_write_timeout_ms=_observability_int(
                "A2A_OBSERVABILITY_MONGO_WRITE_TIMEOUT_MS", 5_000
            ),
            observability_queue_max_items=_observability_int(
                "A2A_OBSERVABILITY_QUEUE_MAX_ITEMS", 2_048
            ),
            observability_queue_max_bytes=_observability_int(
                "A2A_OBSERVABILITY_QUEUE_MAX_BYTES", 8 * 1024 * 1024
            ),
            observability_document_max_bytes=_observability_int(
                "A2A_OBSERVABILITY_DOCUMENT_MAX_BYTES", 16 * 1024
            ),
            observability_batch_size=_observability_int("A2A_OBSERVABILITY_BATCH_SIZE", 100),
            observability_batch_interval_ms=_observability_int(
                "A2A_OBSERVABILITY_BATCH_INTERVAL_MS", 250
            ),
            observability_max_attempts=_observability_int("A2A_OBSERVABILITY_MAX_ATTEMPTS", 3),
            observability_retry_base_delay_ms=_observability_int(
                "A2A_OBSERVABILITY_RETRY_BASE_DELAY_MS", 50
            ),
            observability_shutdown_timeout_ms=_observability_int(
                "A2A_OBSERVABILITY_SHUTDOWN_TIMEOUT_MS", 2_000
            ),
            langsmith_api_key=os.getenv("A2A_LANGSMITH_API_KEY") or None,
            langsmith_project=os.getenv("A2A_LANGSMITH_PROJECT", "argos-ai-service"),
            langsmith_endpoint=os.getenv("A2A_LANGSMITH_ENDPOINT") or None,
            langsmith_workspace_id=os.getenv("A2A_LANGSMITH_WORKSPACE_ID") or None,
            langsmith_capture_content=_environment_bool("A2A_LANGSMITH_CAPTURE_CONTENT"),
        )

    def validate_runtime(
        self,
        *,
        require_providers: bool = True,
        require_permission_sql: bool = True,
    ) -> None:
        if self.answer_thinking not in {"auto", "enabled", "disabled"}:
            raise ValueError("A2A_ANSWER_THINKING deve ser auto, enabled ou disabled")
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
        if require_permission_sql:
            required["A2A_PERMISSION_SQL_DSN"] = self.permission_sql_dsn
        if self.model_mode not in {"provider", "simulated"}:
            raise ValueError("A2A_MODEL_MODE deve ser provider ou simulated")
        if require_providers:
            required.update(
                {
                    "A2A_EMBEDDINGS_API_URL": self.embeddings_api_url,
                    "A2A_EMBEDDINGS_API_KEY": self.embeddings_api_key,
                    "A2A_EMBEDDINGS_MODEL": self.embeddings_model,
                    "A2A_EMBEDDINGS_MODEL_REVISION": self.embeddings_model_revision,
                    "A2A_ANSWER_API_URL": self.answer_api_url,
                    "A2A_ANSWER_API_KEY": self.answer_api_key,
                    "A2A_ANSWER_MODEL": self.answer_model,
                }
            )
        missing = [
            name
            for name, value in required.items()
            if value is None or isinstance(value, str) and not value.strip()
        ]
        if not self.trusted_delegation_keys:
            missing.append("A2A_TRUSTED_DELEGATION_KEYS")
        if (self.observability_mongo_uri is None) != (self.observability_collection is None):
            missing.append(
                "A2A_OBSERVABILITY_MONGO_URI e A2A_OBSERVABILITY_COLLECTION "
                "devem ser configuradas juntas"
            )
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
        if (
            not math.isfinite(self.model_timeout_seconds)
            or not 0 < self.model_timeout_seconds <= 45
        ):
            raise ValueError("A2A_MODEL_TIMEOUT_SECONDS deve ser maior que 0 e até 45")
        if not 128 <= self.model_max_output_tokens <= 8_192:
            raise ValueError("A2A_MODEL_MAX_OUTPUT_TOKENS deve estar entre 128 e 8192")
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
        provider_urls = (
            (
                ("A2A_EMBEDDINGS_API_URL", self.embeddings_api_url, "/embeddings"),
                ("A2A_ANSWER_API_URL", self.answer_api_url, "/chat/completions"),
            )
            if require_providers
            else ()
        )
        for name, value, expected_path in provider_urls:
            try:
                parsed = urlsplit(value)
                local_http = parsed.scheme == "http" and parsed.hostname in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                }
                valid = (
                    (parsed.scheme == "https" or local_http)
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                )
                valid = (
                    valid
                    and parsed.path.rstrip("/").endswith(expected_path)
                    and not parsed.query
                    and not parsed.fragment
                    and (parsed.port is None or 1 <= parsed.port <= 65535)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{name} deve ser HTTPS e terminar em {expected_path}")
        model_names = (
            (
                ("A2A_EMBEDDINGS_MODEL", self.embeddings_model),
                ("A2A_EMBEDDINGS_MODEL_REVISION", self.embeddings_model_revision),
                ("A2A_ANSWER_MODEL", self.answer_model),
            )
            if require_providers
            else ()
        )
        for name, value in model_names:
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} deve ser texto não vazio de até 200 caracteres")
        if any(char.isspace() for char in self.internal_service_token):
            raise ValueError("A2A_INTERNAL_SERVICE_TOKEN não pode conter espaços")
        if not self.observability_mongo_database.strip():
            raise ValueError("A2A_OBSERVABILITY_MONGO_DATABASE não pode estar vazio")
        if not self.langsmith_project.strip():
            raise ValueError("A2A_LANGSMITH_PROJECT não pode estar vazio")
        if self.langsmith_capture_content and not self.langsmith_api_key:
            raise ValueError("A2A_LANGSMITH_CAPTURE_CONTENT exige A2A_LANGSMITH_API_KEY")
        if self.langsmith_endpoint is not None:
            try:
                langsmith_endpoint = urlsplit(self.langsmith_endpoint)
                valid_langsmith_endpoint = (
                    langsmith_endpoint.scheme == "https"
                    and bool(langsmith_endpoint.hostname)
                    and langsmith_endpoint.username is None
                    and langsmith_endpoint.password is None
                    and not langsmith_endpoint.query
                    and not langsmith_endpoint.fragment
                )
            except ValueError:
                valid_langsmith_endpoint = False
            if not valid_langsmith_endpoint:
                raise ValueError("A2A_LANGSMITH_ENDPOINT deve usar HTTPS")
        if self.observability_collection is not None:
            collection = self.observability_collection
            if (
                not collection
                or collection.strip() != collection
                or collection.startswith("system.")
                or "$" in collection
                or "\x00" in collection
                or len(collection.encode("utf-8")) > 120
            ):
                raise ValueError("A2A_OBSERVABILITY_COLLECTION inválida")
            try:
                mongo_uri = urlsplit(self.observability_mongo_uri or "")
                valid_mongo_uri = mongo_uri.scheme in {"mongodb", "mongodb+srv"} and bool(
                    mongo_uri.hostname
                )
                valid_mongo_uri = valid_mongo_uri and (
                    mongo_uri.port is None or 1 <= mongo_uri.port <= 65535
                )
            except ValueError:
                valid_mongo_uri = False
            if not valid_mongo_uri:
                raise ValueError("A2A_OBSERVABILITY_MONGO_URI deve ser uma URI Mongo válida")
        limits = (
            self.observability_mongo_server_selection_timeout_ms,
            self.observability_mongo_write_timeout_ms,
            self.observability_queue_max_items,
            self.observability_queue_max_bytes,
            self.observability_document_max_bytes,
            self.observability_batch_size,
            self.observability_batch_interval_ms,
            self.observability_max_attempts,
            self.observability_shutdown_timeout_ms,
        )
        if min(limits) < 1 or self.observability_retry_base_delay_ms < 0:
            raise ValueError("limites de observabilidade A2A inválidos")
        if (
            self.observability_batch_size > self.observability_queue_max_items
            or self.observability_document_max_bytes > self.observability_queue_max_bytes
        ):
            raise ValueError("limites do lote ou documento excedem a fila A2A")
