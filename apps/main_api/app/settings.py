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
    app_env: str = "production"
    mongo_uri: str | None = None
    auth_database_url: str | None = field(default=None, repr=False)
    auth_database_pool_min_size: int = 1
    auth_database_pool_max_size: int = 5
    auth_database_connect_timeout_seconds: float = 3.0
    auth_database_query_timeout_seconds: float = 3.0
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
    auth_mode: str = "jwt"
    firebase_project_id: str | None = None
    firebase_credentials_file: str | None = field(default=None, repr=False)
    access_token_issuer: str = "argos-ai-service"
    access_token_audience: str = "argos-ai-service-api"
    access_token_key_id: str | None = None
    access_token_private_key: str | None = field(default=None, repr=False)
    access_token_ttl_seconds: int = 900
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
    document_a2a_timeout_seconds: float = 45.0
    document_a2a_response_max_bytes: int = 1_000_000
    model_api_base_url: str | None = None
    model_mode: str = "provider"
    model_api_key: str | None = field(default=None, repr=False)
    model_router_name: str | None = None
    model_farm_name: str | None = None
    model_doctor_name: str | None = None
    model_analyst_name: str | None = None
    model_secretary_name: str | None = None
    model_information_name: str | None = None
    model_orchestrator_name: str | None = None
    model_judge_name: str | None = None
    model_question_formatter_name: str | None = None
    model_timeout_seconds: float = 60.0
    model_max_retries: int = 2
    model_max_output_tokens: int = 4096
    model_thinking: str = "auto"
    execution_max_model_calls: int = 12
    execution_max_tool_calls: int = 12
    execution_timeout_seconds: float = 300.0
    langsmith_api_key: str | None = field(default=None, repr=False)
    langsmith_project: str = "argos-ai-service"
    langsmith_endpoint: str | None = None
    langsmith_capture_content: bool = False
    langsmith_workspace_id: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
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
            app_env=os.getenv("APP_ENV", "production").strip().casefold(),
            mongo_uri=os.getenv("MAIN_API_MONGO_URI") or None,
            auth_database_url=os.getenv("MAIN_API_AUTH_DATABASE_URL") or None,
            auth_database_pool_min_size=int(os.getenv("MAIN_API_AUTH_DATABASE_POOL_MIN_SIZE", "1")),
            auth_database_pool_max_size=int(os.getenv("MAIN_API_AUTH_DATABASE_POOL_MAX_SIZE", "5")),
            auth_database_connect_timeout_seconds=float(
                os.getenv("MAIN_API_AUTH_DATABASE_CONNECT_TIMEOUT_SECONDS", "3")
            ),
            auth_database_query_timeout_seconds=float(
                os.getenv("MAIN_API_AUTH_DATABASE_QUERY_TIMEOUT_SECONDS", "3")
            ),
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
            auth_mode=os.getenv("AUTH_MODE", "jwt").strip().casefold(),
            firebase_project_id=os.getenv("MAIN_API_FIREBASE_PROJECT_ID") or None,
            firebase_credentials_file=os.getenv("MAIN_API_FIREBASE_CREDENTIALS_FILE") or None,
            access_token_issuer=os.getenv("MAIN_API_ACCESS_TOKEN_ISSUER", "argos-ai-service"),
            access_token_audience=os.getenv(
                "MAIN_API_ACCESS_TOKEN_AUDIENCE", "argos-ai-service-api"
            ),
            access_token_key_id=os.getenv("MAIN_API_ACCESS_TOKEN_KEY_ID") or None,
            access_token_private_key=os.getenv("MAIN_API_ACCESS_TOKEN_PRIVATE_KEY") or None,
            access_token_ttl_seconds=int(os.getenv("MAIN_API_ACCESS_TOKEN_TTL_SECONDS", "900")),
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
                os.getenv("MAIN_API_DOCUMENT_A2A_TIMEOUT_SECONDS", "45")
            ),
            document_a2a_response_max_bytes=int(
                os.getenv("MAIN_API_DOCUMENT_A2A_RESPONSE_MAX_BYTES", "1000000")
            ),
            model_api_base_url=os.getenv("MAIN_API_MODEL_API_BASE_URL") or None,
            model_mode=os.getenv("MAIN_API_MODEL_MODE", "provider").strip().casefold(),
            model_api_key=os.getenv("MAIN_API_MODEL_API_KEY") or None,
            model_router_name=os.getenv("MAIN_API_MODEL_ROUTER") or None,
            model_farm_name=os.getenv("MAIN_API_MODEL_FARM") or None,
            model_doctor_name=os.getenv("MAIN_API_MODEL_DOCTOR") or None,
            model_analyst_name=os.getenv("MAIN_API_MODEL_ANALYST") or None,
            model_secretary_name=os.getenv("MAIN_API_MODEL_SECRETARY") or None,
            model_information_name=os.getenv("MAIN_API_MODEL_INFORMATION") or None,
            model_orchestrator_name=os.getenv("MAIN_API_MODEL_ORCHESTRATOR") or None,
            model_judge_name=os.getenv("MAIN_API_MODEL_JUDGE") or None,
            model_question_formatter_name=os.getenv("MAIN_API_MODEL_QUESTION_FORMATTER") or None,
            model_timeout_seconds=float(os.getenv("MAIN_API_MODEL_TIMEOUT_SECONDS", "60")),
            model_max_retries=int(os.getenv("MAIN_API_MODEL_MAX_RETRIES", "2")),
            model_max_output_tokens=int(os.getenv("MAIN_API_MODEL_MAX_OUTPUT_TOKENS", "4096")),
            model_thinking=os.getenv("MAIN_API_MODEL_THINKING", "auto").strip().casefold(),
            execution_max_model_calls=int(os.getenv("MAIN_API_EXECUTION_MAX_MODEL_CALLS", "12")),
            execution_max_tool_calls=int(os.getenv("MAIN_API_EXECUTION_MAX_TOOL_CALLS", "12")),
            execution_timeout_seconds=float(os.getenv("MAIN_API_EXECUTION_TIMEOUT_SECONDS", "300")),
            langsmith_api_key=os.getenv("MAIN_API_LANGSMITH_API_KEY") or None,
            langsmith_project=os.getenv("MAIN_API_LANGSMITH_PROJECT", "argos-ai-service"),
            langsmith_endpoint=os.getenv("MAIN_API_LANGSMITH_ENDPOINT") or None,
            langsmith_workspace_id=os.getenv("MAIN_API_LANGSMITH_WORKSPACE_ID") or None,
            langsmith_capture_content=_environment_bool("MAIN_API_LANGSMITH_CAPTURE_CONTENT"),
        )

    def __post_init__(self) -> None:
        if not self.mongo_database.strip():
            raise ValueError("MAIN_API_MONGO_DATABASE não pode estar vazio")
        if self.auth_database_url is not None and not self.auth_database_url.strip():
            raise ValueError("MAIN_API_AUTH_DATABASE_URL não pode estar vazio")
        if self.firebase_project_id is not None and not self.firebase_project_id.strip():
            raise ValueError("MAIN_API_FIREBASE_PROJECT_ID não pode estar vazio")
        if (
            self.firebase_credentials_file is not None
            and not self.firebase_credentials_file.strip()
        ):
            raise ValueError("MAIN_API_FIREBASE_CREDENTIALS_FILE não pode estar vazio")
        if self.firebase_credentials_file is not None and self.firebase_project_id is None:
            raise ValueError(
                "MAIN_API_FIREBASE_PROJECT_ID é obrigatório com credencial Firebase explícita"
            )
        if bool(self.access_token_key_id) != bool(self.access_token_private_key):
            raise ValueError(
                "MAIN_API_ACCESS_TOKEN_KEY_ID e MAIN_API_ACCESS_TOKEN_PRIVATE_KEY "
                "devem ser configurados juntos"
            )
        if self.firebase_project_id is not None and not self.access_token_private_key:
            raise ValueError(
                "autenticação Firebase exige MAIN_API_ACCESS_TOKEN_KEY_ID e "
                "MAIN_API_ACCESS_TOKEN_PRIVATE_KEY"
            )
        if (
            self.auth_database_pool_min_size < 1
            or self.auth_database_pool_max_size < self.auth_database_pool_min_size
            or self.auth_database_pool_max_size > 20
            or not 0 < self.auth_database_connect_timeout_seconds <= 30
            or not 0 < self.auth_database_query_timeout_seconds <= 30
        ):
            raise ValueError("limites e timeouts de conexão PostgreSQL são inválidos")
        if self.model_thinking not in {"auto", "enabled", "disabled"}:
            raise ValueError("MAIN_API_MODEL_THINKING deve ser auto, enabled ou disabled")
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
            or not self.access_token_issuer.strip()
            or not self.access_token_audience.strip()
            or not 60 <= self.access_token_ttl_seconds <= 3600
            or not self.mcp_delegation_issuer.strip()
            or not 1 <= self.mcp_delegation_ttl_seconds <= 300
            or not 0 < self.document_a2a_timeout_seconds <= 60
            or self.document_a2a_response_max_bytes < 1
            or not 0 < self.model_timeout_seconds <= 120
            or not 0 <= self.model_max_retries <= 2
            or not 1 <= self.model_max_output_tokens <= 8192
            or not 1 <= self.execution_max_model_calls <= 100
            or not 1 <= self.execution_max_tool_calls <= 100
            or not 1 <= self.execution_timeout_seconds <= 300
            or not self.langsmith_project.strip()
        ):
            raise ValueError("limites e timeouts devem ser positivos")
        if self.model_mode not in {"provider", "simulated"}:
            raise ValueError("MAIN_API_MODEL_MODE deve ser provider ou simulated")
        if self.auth_mode not in {"jwt", "stub"}:
            raise ValueError("AUTH_MODE deve ser jwt ou stub")
        if self.auth_mode == "stub" and self.app_env != "local":
            raise ValueError("AUTH_MODE=stub só pode ser usado com APP_ENV=local")
        if self.model_api_base_url is not None:
            from urllib.parse import urlsplit

            try:
                parsed_model_url = urlsplit(self.model_api_base_url)
                local_http = parsed_model_url.scheme == "http" and parsed_model_url.hostname in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                }
                valid_model_url = (
                    (parsed_model_url.scheme == "https" or local_http)
                    and bool(parsed_model_url.hostname)
                    and parsed_model_url.username is None
                    and parsed_model_url.password is None
                    and not parsed_model_url.query
                    and not parsed_model_url.fragment
                    and (parsed_model_url.port is None or 1 <= parsed_model_url.port <= 65535)
                )
            except ValueError:
                valid_model_url = False
            if not valid_model_url:
                raise ValueError(
                    "MAIN_API_MODEL_API_BASE_URL deve ser HTTPS ou HTTP local sem credenciais"
                )
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
                local_http = parsed.scheme == "http" and (
                    parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                    or self.app_env == "local"
                    and parsed.hostname in {"a2a_agent", "a2a-agent"}
                )
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
