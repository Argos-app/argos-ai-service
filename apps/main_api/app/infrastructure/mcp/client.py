"""Cliente MCP de curta duração para transporte Streamable HTTP."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.application.operation_observation import active_collector, observe_operation
from app.settings import Settings
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ProtocolMetadata,
    current_correlation_id,
)

MCP_TOOL_ALLOWLISTS: Mapping[str, frozenset[str]] = {
    "data": frozenset(
        {
            "list_properties",
            "list_herds",
            "list_lots",
            "get_lot_report_source",
            "list_exception_animals",
            "list_applications",
            "list_application_animals",
            "list_occurrences",
            "list_animal_lot_history",
            "list_cattle_documents",
            "get_farm_summary",
            "summarize_exception_animals",
            "list_medicines",
            "list_medicine_documents",
        }
    ),
    "prediction": frozenset({"estimate_medicine_consumption"}),
    "email": frozenset({"prepare_report", "send_report", "get_report_status"}),
}


class McpClientConfigurationError(ValueError):
    """Endpoint ou credencial MCP ausente ou incompatível com o serviço."""


class McpConnectionError(RuntimeError):
    """Não foi possível inicializar ou comunicar com um servidor MCP."""


class McpToolNotAllowedError(ValueError):
    """A ferramenta solicitada não pertence à allowlist do servidor."""


class McpToolExecutionError(RuntimeError):
    """O servidor respondeu com um erro durante a execução da ferramenta."""


class McpResponseTooLargeError(RuntimeError):
    """A resposta MCP excedeu o limite configurado para o principal."""


def _validate_endpoint(
    url: str,
    service_name: str,
    *,
    allow_local_http: bool = False,
) -> None:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise McpClientConfigurationError(f"URL inválida para o MCP {service_name}") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.path.rstrip("/") != "/mcp"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise McpClientConfigurationError(
            f"MAIN_API_MCP_{service_name.upper()}_URL deve apontar para o endpoint /mcp"
        )
    if parsed.scheme == "http":
        try:
            is_loopback = (
                hostname.casefold() == "localhost" or ipaddress.ip_address(hostname).is_loopback
            )
        except ValueError:
            is_loopback = False
        local_service_hosts = {
            "data": {"mcp_data", "mcp-data"},
            "prediction": {"mcp_prediction", "mcp-prediction"},
            "email": {"mcp_email", "mcp-email"},
        }
        if not is_loopback and not (
            allow_local_http and hostname.casefold() in local_service_hosts[service_name]
        ):
            raise McpClientConfigurationError(
                f"o MCP {service_name} deve usar HTTPS fora do loopback"
            )
    if port is not None and not 1 <= port <= 65535:
        raise McpClientConfigurationError(f"porta inválida para o MCP {service_name}")


@dataclass(frozen=True, slots=True)
class McpToolServerClient:
    """Abre uma sessão MCP por operação e fecha todo recurso ao concluí-la."""

    service_name: str
    url: str
    service_token: str = field(repr=False)
    allowed_tools: frozenset[str]
    timeout_seconds: float = 10.0
    response_max_bytes: int = 1_000_000
    allow_local_http: bool = field(default=False, repr=False, compare=False)
    observation_collector: ObservationCollector | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.service_name not in MCP_TOOL_ALLOWLISTS:
            raise McpClientConfigurationError(f"serviço MCP desconhecido: {self.service_name}")
        _validate_endpoint(
            self.url,
            self.service_name,
            allow_local_http=self.allow_local_http,
        )
        if not self.service_token.strip():
            raise McpClientConfigurationError(f"token do MCP {self.service_name} está vazio")
        if (
            not self.allowed_tools
            or not self.allowed_tools <= MCP_TOOL_ALLOWLISTS[self.service_name]
        ):
            raise McpClientConfigurationError(f"allowlist inválida para o MCP {self.service_name}")
        if self.timeout_seconds <= 0 or self.response_max_bytes < 1:
            raise McpClientConfigurationError("timeout e limite de resposta devem ser positivos")

    @asynccontextmanager
    async def _session(self, request_headers: Mapping[str, str] | None = None):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {self.service_token}"}
        supported_headers = {"x-argos-delegation", "x-argos-correlation-id"}
        seen_headers: set[str] = set()
        for name, value in (request_headers or {}).items():
            normalized_name = name.casefold()
            if normalized_name not in supported_headers or not value.strip():
                raise ValueError("header variável não permitido na chamada MCP")
            if normalized_name in seen_headers:
                raise ValueError("header duplicado na chamada MCP")
            seen_headers.add(normalized_name)
            if normalized_name == "x-argos-correlation-id":
                try:
                    parsed_correlation_id = UUID(value)
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValueError("correlation ID inválido na chamada MCP") from exc
                if str(parsed_correlation_id) != value.lower():
                    raise ValueError("correlation ID deve usar formato UUID canônico")
                active_correlation_id = current_correlation_id()
                if active_correlation_id and parsed_correlation_id != active_correlation_id:
                    raise ValueError("correlation ID diverge do contexto da chamada")
            headers[
                "X-Argos-Delegation"
                if normalized_name == "x-argos-delegation"
                else "X-Argos-Correlation-ID"
            ] = value
        correlation_id = current_correlation_id()
        if correlation_id is not None:
            headers.setdefault("X-Argos-Correlation-ID", str(correlation_id))

        options = {
            "operation_kind": OperationKind.MCP,
            "operation_name": f"mcp.{self.service_name}.session",
            "operation_phase": OperationPhase.INITIALIZATION,
            "observation_level": ObservationLevel.TRANSPORT_ATTEMPT,
            "protocol": ProtocolMetadata(name="mcp", operation="session.initialize"),
            "timeout_ms": self.timeout_seconds * 1000,
            "error_origin": ErrorOrigin.TRANSPORT,
        }
        recorder_context = (
            observe_operation(self.observation_collector, **options)
            if self.observation_collector is not None
            else nullcontext(None)
        )
        async with recorder_context as recorder:
            try:
                async with self._open_session(
                    headers, ClientSession, streamable_http_client
                ) as session:
                    yield session
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                if recorder is not None:
                    recorder.finish(error=exc)
                raise McpConnectionError(
                    f"falha de transporte ao comunicar com o MCP {self.service_name}"
                ) from exc
            except Exception as exc:
                from mcp.shared.exceptions import McpError

                if isinstance(exc, McpError):
                    if recorder is not None:
                        recorder.finish(error=exc)
                    raise McpConnectionError(
                        f"falha ao inicializar a sessão MCP {self.service_name}"
                    ) from exc
                if recorder is not None:
                    recorder.finish(error=exc)
                raise

    @asynccontextmanager
    async def _open_session(self, headers, ClientSession, streamable_http_client):
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                ) as session:
                    await session.initialize()
                    yield session

    def _check_response_size(self, response: Any) -> None:
        serialized = json.dumps(
            response.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > self.response_max_bytes:
            raise McpResponseTooLargeError(
                f"resposta do MCP {self.service_name} excedeu o limite configurado"
            )

    async def list_tools(self) -> tuple[Any, ...]:
        """Lista apenas as ferramentas conhecidas e aprovadas para este serviço."""
        async with self._observed_mcp_operation("list_tools", phase=OperationPhase.INITIALIZATION):
            try:
                async with self._session() as session:
                    result = await session.list_tools()
            except McpConnectionError:
                raise
            self._check_response_size(result)
            available = {tool.name: tool for tool in result.tools}
            return tuple(available[name] for name in sorted(self.allowed_tools & available.keys()))

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Executa uma ferramenta permitida sem retry automático."""
        safe_name = name if name in MCP_TOOL_ALLOWLISTS[self.service_name] else "unknown"
        async with self._observed_mcp_operation(f"call_tool.{safe_name}"):
            if name not in self.allowed_tools:
                raise McpToolNotAllowedError(
                    f"ferramenta {name!r} não está autorizada no MCP {self.service_name}"
                )
            if not isinstance(arguments, Mapping):
                raise TypeError("arguments deve ser um objeto")

            try:
                propagated_headers = dict(request_headers or {})
                correlation_id = current_correlation_id()
                if correlation_id is not None and not any(
                    name.casefold() == "x-argos-correlation-id" for name in propagated_headers
                ):
                    propagated_headers["X-Argos-Correlation-ID"] = str(correlation_id)
                async with self._session(propagated_headers) as session:
                    result = await session.call_tool(name, dict(arguments))
            except McpConnectionError:
                raise
            self._check_response_size(result)
            if result.isError:
                raise McpToolExecutionError(
                    f"a ferramenta {name!r} retornou erro no MCP {self.service_name}"
                )
            return result

    @asynccontextmanager
    async def _observed_mcp_operation(self, operation: str, *, phase=OperationPhase.EXECUTION):
        options = {
            "operation_kind": OperationKind.MCP,
            "operation_name": f"mcp.{self.service_name}.{operation}"[:120],
            "operation_phase": phase,
            "observation_level": ObservationLevel.SDK_INVOCATION,
            "protocol": ProtocolMetadata(name="mcp", operation=operation[:120]),
            "timeout_ms": self.timeout_seconds * 1000,
            "error_origin": ErrorOrigin.TRANSPORT,
        }
        if self.observation_collector is None:
            yield
            return
        async with observe_operation(self.observation_collector, **options) as recorder:
            try:
                yield
            except McpToolExecutionError as exc:
                recorder.finish(error=exc, error_origin=ErrorOrigin.PROTOCOL)
                raise


def bind_observation_collector(
    clients: McpClients,
    collector: ObservationCollector,
) -> McpClients:
    """Clona os clientes configurados com o coletor runtime da aplicação."""
    active = active_collector(collector)
    return McpClients(
        data=(
            replace(clients.data, observation_collector=active)
            if clients.data is not None
            else None
        ),
        prediction=(
            replace(clients.prediction, observation_collector=active)
            if clients.prediction is not None
            else None
        ),
        email=(
            replace(clients.email, observation_collector=active)
            if clients.email is not None
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class McpClients:
    """Clientes configurados por serviço, sem compartilhar sessão ou credencial."""

    data: McpToolServerClient | None = None
    prediction: McpToolServerClient | None = None
    email: McpToolServerClient | None = None


def create_mcp_clients(settings: Settings) -> McpClients:
    """Compõe os clientes configurados; servidores ausentes continuam opcionais."""
    clients: dict[str, McpToolServerClient | None] = {}
    definitions = (
        ("data", settings.mcp_data_url, settings.mcp_data_token),
        ("prediction", settings.mcp_prediction_url, settings.mcp_prediction_token),
        ("email", settings.mcp_email_url, settings.mcp_email_token),
    )
    for service_name, url, token in definitions:
        if not url and not token:
            clients[service_name] = None
            continue
        if not url or not token:
            raise McpClientConfigurationError(
                f"URL e token são obrigatórios para configurar o MCP {service_name}"
            )
        clients[service_name] = McpToolServerClient(
            service_name=service_name,
            url=url,
            service_token=token,
            allowed_tools=MCP_TOOL_ALLOWLISTS[service_name],
            timeout_seconds=settings.mcp_timeout_seconds,
            response_max_bytes=settings.mcp_response_max_bytes,
            allow_local_http=settings.app_env == "local",
        )
    return McpClients(**clients)
