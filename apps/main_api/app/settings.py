"""Configuração externa do serviço principal."""

import json
import logging
import os
import re
from dataclasses import dataclass, field, fields

from app.session_config import SESSION_SUMMARY_INPUT_MAX_TOKENS

_LOGGER = logging.getLogger(__name__)


def _observability_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _LOGGER.warning(
            "invalid observability setting; using default",
            extra={"setting": name},
        )
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


_MONGO_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


@dataclass(frozen=True, slots=True)
class MongoCollectionSettings:
    """Nomes das collections pertencentes ao serviço principal."""

    sessions: str = "ai_sessions"
    interactions: str = "ai_interactions"
    messages: str = "ai_messages"
    execution_events: str = "ai_execution_events"
    session_summaries: str = "ai_session_summaries"
    user_memory: str = "ai_user_memory"
    report_artifacts: str = "ai_report_artifacts"
    email_preparations: str = "ai_email_preparations"
    pending_recipient_resolutions: str = "ai_pending_recipient_resolutions"
    email_send_authorizations: str = "ai_email_send_authorizations"
    email_operation_references: str = "ai_email_operation_references"
    langgraph_checkpoints: str = "ai_langgraph_checkpoints"
    langgraph_checkpoint_writes: str = "ai_langgraph_checkpoint_writes"

    def __post_init__(self) -> None:
        names = [getattr(self, item.name) for item in fields(self)]
        for name in names:
            if not _MONGO_COLLECTION_NAME.fullmatch(name) or name.startswith("system."):
                raise ValueError("nome de collection Mongo inválido")
        if len(names) != len(set(names)):
            raise ValueError("collections Mongo do main_api devem ter nomes distintos")

    @classmethod
    def from_env(cls) -> MongoCollectionSettings:
        defaults = cls()
        return cls(
            **{
                item.name: os.getenv(
                    f"MAIN_API_MONGO_COLLECTION_{item.name.upper()}",
                    getattr(defaults, item.name),
                )
                for item in fields(cls)
            }
        )


@dataclass(frozen=True, slots=True)
class Settings:
    mongo_uri: str | None = None
    mongo_database: str = "argos_ai"
    mongo_collections: MongoCollectionSettings = field(default_factory=MongoCollectionSettings)
    mongo_server_selection_timeout_ms: int = 5_000
    mongo_write_timeout_ms: int = 5_000
    observability_collection: str | None = None
    observability_queue_max_items: int = 2_048
    observability_queue_max_bytes: int = 8 * 1024 * 1024
    observability_document_max_bytes: int = 16 * 1024
    observability_batch_size: int = 100
    observability_batch_interval_ms: int = 250
    observability_max_attempts: int = 3
    observability_retry_base_delay_ms: int = 50
    observability_shutdown_timeout_ms: int = 2_000
    session_summary_input_max_tokens: int = SESSION_SUMMARY_INPUT_MAX_TOKENS
    auth_issuer: str = "argos-backend-service"
    auth_audience: str = "argos-ai-service"
    auth_trusted_public_keys: tuple[tuple[str, str], ...] = ()
    auth_token_max_ttl_seconds: int = 300
    auth_clock_skew_seconds: int = 30
    mcp_data_url: str | None = None
    mcp_data_token: str | None = field(default=None, repr=False)
    mcp_data_report_trusted_signature_keys: tuple[tuple[str, str], ...] = ()
    mcp_prediction_url: str | None = None
    mcp_prediction_token: str | None = field(default=None, repr=False)
    mcp_email_url: str | None = None
    mcp_email_token: str | None = field(default=None, repr=False)
    mcp_timeout_seconds: float = 10.0
    mcp_response_max_bytes: int = 1_000_000
    mcp_delegation_issuer: str = "argos-main-service"
    mcp_delegation_key_id: str | None = None
    mcp_delegation_private_key: str | None = field(default=None, repr=False)
    mcp_delegation_ttl_seconds: int = 240
    document_a2a_url: str | None = None
    document_a2a_service_token: str | None = field(default=None, repr=False)
    document_a2a_timeout_seconds: float = 25.0
    document_a2a_response_max_bytes: int = 1_000_000
    langsmith_api_key: str | None = field(default=None, repr=False)
    langsmith_project: str = "argos-ai-service"
    langsmith_endpoint: str | None = None
    langsmith_capture_content: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        raw_auth_keys = os.getenv("MAIN_API_AUTH_TRUSTED_PUBLIC_KEYS", "{}")
        try:
            auth_keys = json.loads(raw_auth_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("MAIN_API_AUTH_TRUSTED_PUBLIC_KEYS deve ser JSON") from exc
        if not isinstance(auth_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in auth_keys.items()
        ):
            raise ValueError(
                "MAIN_API_AUTH_TRUSTED_PUBLIC_KEYS deve mapear IDs para chaves públicas Base64"
            )
        raw_report_keys = os.getenv("MAIN_API_MCP_DATA_REPORT_TRUSTED_SIGNATURE_KEYS", "{}")
        try:
            report_keys = json.loads(raw_report_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "MAIN_API_MCP_DATA_REPORT_TRUSTED_SIGNATURE_KEYS deve ser JSON"
            ) from exc
        if not isinstance(report_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in report_keys.items()
        ):
            raise ValueError(
                "MAIN_API_MCP_DATA_REPORT_TRUSTED_SIGNATURE_KEYS deve mapear IDs para chaves Base64"
            )

        return cls(
            mongo_uri=os.getenv("MAIN_API_MONGO_URI") or None,
            mongo_database=os.getenv("MAIN_API_MONGO_DATABASE", "argos_ai"),
            mongo_collections=MongoCollectionSettings.from_env(),
            mongo_server_selection_timeout_ms=int(
                os.getenv("MAIN_API_MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")
            ),
            mongo_write_timeout_ms=int(os.getenv("MAIN_API_MONGO_WRITE_TIMEOUT_MS", "5000")),
            observability_collection=os.getenv("MAIN_API_OBSERVABILITY_COLLECTION") or None,
            observability_queue_max_items=_observability_int(
                "MAIN_API_OBSERVABILITY_QUEUE_MAX_ITEMS", 2048
            ),
            observability_queue_max_bytes=_observability_int(
                "MAIN_API_OBSERVABILITY_QUEUE_MAX_BYTES", 8 * 1024 * 1024
            ),
            observability_document_max_bytes=_observability_int(
                "MAIN_API_OBSERVABILITY_DOCUMENT_MAX_BYTES", 16 * 1024
            ),
            observability_batch_size=_observability_int("MAIN_API_OBSERVABILITY_BATCH_SIZE", 100),
            observability_batch_interval_ms=_observability_int(
                "MAIN_API_OBSERVABILITY_BATCH_INTERVAL_MS", 250
            ),
            observability_max_attempts=_observability_int("MAIN_API_OBSERVABILITY_MAX_ATTEMPTS", 3),
            observability_retry_base_delay_ms=_observability_int(
                "MAIN_API_OBSERVABILITY_RETRY_BASE_DELAY_MS", 50
            ),
            observability_shutdown_timeout_ms=_observability_int(
                "MAIN_API_OBSERVABILITY_SHUTDOWN_TIMEOUT_MS", 2000
            ),
            session_summary_input_max_tokens=int(
                os.getenv(
                    "MAIN_API_SESSION_SUMMARY_INPUT_MAX_TOKENS",
                    str(SESSION_SUMMARY_INPUT_MAX_TOKENS),
                )
            ),
            auth_issuer=os.getenv("MAIN_API_AUTH_ISSUER", "argos-backend-service"),
            auth_audience=os.getenv("MAIN_API_AUTH_AUDIENCE", "argos-ai-service"),
            auth_trusted_public_keys=tuple(auth_keys.items()),
            auth_token_max_ttl_seconds=int(os.getenv("MAIN_API_AUTH_TOKEN_MAX_TTL_SECONDS", "300")),
            auth_clock_skew_seconds=int(os.getenv("MAIN_API_AUTH_CLOCK_SKEW_SECONDS", "30")),
            mcp_data_url=os.getenv("MAIN_API_MCP_DATA_URL") or None,
            mcp_data_token=os.getenv("MAIN_API_MCP_DATA_TOKEN") or None,
            mcp_data_report_trusted_signature_keys=tuple(report_keys.items()),
            mcp_prediction_url=os.getenv("MAIN_API_MCP_PREDICTION_URL") or None,
            mcp_prediction_token=os.getenv("MAIN_API_MCP_PREDICTION_TOKEN") or None,
            mcp_email_url=os.getenv("MAIN_API_MCP_EMAIL_URL") or None,
            mcp_email_token=os.getenv("MAIN_API_MCP_EMAIL_TOKEN") or None,
            mcp_timeout_seconds=float(os.getenv("MAIN_API_MCP_TIMEOUT_SECONDS", "10")),
            mcp_response_max_bytes=int(os.getenv("MAIN_API_MCP_RESPONSE_MAX_BYTES", "1000000")),
            mcp_delegation_issuer=os.getenv("MAIN_API_MCP_DELEGATION_ISSUER", "argos-main-service"),
            mcp_delegation_key_id=os.getenv("MAIN_API_MCP_DELEGATION_KEY_ID") or None,
            mcp_delegation_private_key=os.getenv("MAIN_API_MCP_DELEGATION_PRIVATE_KEY") or None,
            mcp_delegation_ttl_seconds=int(os.getenv("MAIN_API_MCP_DELEGATION_TTL_SECONDS", "240")),
            document_a2a_url=os.getenv("MAIN_API_DOCUMENT_A2A_URL") or None,
            document_a2a_service_token=os.getenv("MAIN_API_DOCUMENT_A2A_SERVICE_TOKEN") or None,
            document_a2a_timeout_seconds=float(
                os.getenv("MAIN_API_DOCUMENT_A2A_TIMEOUT_SECONDS", "25")
            ),
            document_a2a_response_max_bytes=int(
                os.getenv("MAIN_API_DOCUMENT_A2A_RESPONSE_MAX_BYTES", "1000000")
            ),
            langsmith_api_key=os.getenv("MAIN_API_LANGSMITH_API_KEY") or None,
            langsmith_project=os.getenv("MAIN_API_LANGSMITH_PROJECT", "argos-ai-service"),
            langsmith_endpoint=os.getenv("MAIN_API_LANGSMITH_ENDPOINT") or None,
            langsmith_capture_content=_environment_bool("MAIN_API_LANGSMITH_CAPTURE_CONTENT"),
        )

    def __post_init__(self) -> None:
        if not self.mongo_database.strip():
            raise ValueError("MAIN_API_MONGO_DATABASE não pode estar vazio")
        if self.observability_collection is not None and self.observability_collection in {
            getattr(self.mongo_collections, item.name) for item in fields(self.mongo_collections)
        }:
            raise ValueError("MAIN_API_OBSERVABILITY_COLLECTION colide com collection do main_api")
        if (
            self.mongo_server_selection_timeout_ms < 1
            or self.mongo_write_timeout_ms < 1
            or self.session_summary_input_max_tokens < 1
            or self.mcp_timeout_seconds <= 0
            or self.mcp_response_max_bytes < 1
            or not self.auth_issuer.strip()
            or not self.auth_audience.strip()
            or not 1 <= self.auth_token_max_ttl_seconds <= 300
            or not 0 <= self.auth_clock_skew_seconds <= 60
            or not self.mcp_delegation_issuer.strip()
            or not 1 <= self.mcp_delegation_ttl_seconds <= 300
            or not 0 < self.document_a2a_timeout_seconds <= 30
            or self.document_a2a_response_max_bytes < 1
            or not self.langsmith_project.strip()
        ):
            raise ValueError("limites e timeouts devem ser positivos")
        if self.langsmith_capture_content and not self.langsmith_api_key:
            raise ValueError("MAIN_API_LANGSMITH_CAPTURE_CONTENT exige MAIN_API_LANGSMITH_API_KEY")
        if self.langsmith_endpoint is not None:
            from urllib.parse import urlsplit

            try:
                parsed_langsmith_endpoint = urlsplit(self.langsmith_endpoint)
                valid_langsmith_endpoint = (
                    parsed_langsmith_endpoint.scheme == "https"
                    and bool(parsed_langsmith_endpoint.hostname)
                    and parsed_langsmith_endpoint.username is None
                    and parsed_langsmith_endpoint.password is None
                    and not parsed_langsmith_endpoint.query
                    and not parsed_langsmith_endpoint.fragment
                )
            except ValueError:
                valid_langsmith_endpoint = False
            if not valid_langsmith_endpoint:
                raise ValueError("MAIN_API_LANGSMITH_ENDPOINT deve usar HTTPS")
        if bool(self.mcp_delegation_key_id) != bool(self.mcp_delegation_private_key):
            raise ValueError(
                "MAIN_API_MCP_DELEGATION_KEY_ID e "
                "MAIN_API_MCP_DELEGATION_PRIVATE_KEY devem ser configurados juntos"
            )
        if bool(self.document_a2a_url) != bool(self.document_a2a_service_token):
            raise ValueError(
                "MAIN_API_DOCUMENT_A2A_URL e MAIN_API_DOCUMENT_A2A_SERVICE_TOKEN "
                "devem ser configurados juntos"
            )
        if self.document_a2a_url is not None:
            from urllib.parse import urlsplit

            try:
                parsed = urlsplit(self.document_a2a_url)
                local_http = parsed.scheme == "http" and parsed.hostname in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                }
                valid_url = (
                    (parsed.scheme == "https" or local_http)
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.path.rstrip("/").endswith("/a2a")
                    and not parsed.query
                    and not parsed.fragment
                    and (parsed.port is None or 1 <= parsed.port <= 65535)
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                raise ValueError(
                    "MAIN_API_DOCUMENT_A2A_URL deve usar HTTPS ou HTTP local e terminar em /a2a"
                )
            if not self.mcp_delegation_key_id or not self.mcp_delegation_private_key:
                raise ValueError(
                    "a delegação A2A exige MAIN_API_MCP_DELEGATION_KEY_ID e "
                    "MAIN_API_MCP_DELEGATION_PRIVATE_KEY"
                )
