"""Cliente MCP de curta duração para transporte Streamable HTTP."""

import ipaddress
import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.settings import Settings

MCP_TOOL_ALLOWLISTS: Mapping[str, frozenset[str]] = {
    "data": frozenset(
        {
            "list_properties",
            "list_herds",
            "list_lots",
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


def _validate_endpoint(url: str, service_name: str) -> None:
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
        if not is_loopback:
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

    def __post_init__(self) -> None:
        if self.service_name not in MCP_TOOL_ALLOWLISTS:
            raise McpClientConfigurationError(f"serviço MCP desconhecido: {self.service_name}")
        _validate_endpoint(self.url, self.service_name)
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
        delegation_headers = [
            value
            for name, value in (request_headers or {}).items()
            if name.casefold() == "x-argos-delegation"
        ]
        if len(delegation_headers) > 1 or any(not value.strip() for value in delegation_headers):
            raise ValueError("delegação ausente ou duplicada na chamada MCP")
        for name, value in (request_headers or {}).items():
            if name.casefold() != "x-argos-delegation" or not value.strip():
                raise ValueError("somente o header de delegação pode variar por chamada MCP")
            headers["X-Argos-Delegation"] = value

        timeout = httpx.Timeout(self.timeout_seconds)
        try:
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
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise McpConnectionError(
                f"falha de transporte ao comunicar com o MCP {self.service_name}"
            ) from exc
        except Exception as exc:
            from mcp.shared.exceptions import McpError

            if isinstance(exc, McpError):
                raise McpConnectionError(
                    f"falha ao inicializar a sessão MCP {self.service_name}"
                ) from exc
            raise

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
        if name not in self.allowed_tools:
            raise McpToolNotAllowedError(
                f"ferramenta {name!r} não está autorizada no MCP {self.service_name}"
            )
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments deve ser um objeto")

        try:
            async with self._session(request_headers) as session:
                result = await session.call_tool(name, dict(arguments))
        except McpConnectionError:
            raise
        self._check_response_size(result)
        if result.isError:
            raise McpToolExecutionError(
                f"a ferramenta {name!r} retornou erro no MCP {self.service_name}"
            )
        return result


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
        )
    return McpClients(**clients)
