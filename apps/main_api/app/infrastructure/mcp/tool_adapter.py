"""Adaptação de chamadas MCP autenticadas para ferramentas LangChain."""

from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from app.infrastructure.mcp.client import McpToolServerClient
from app.infrastructure.mcp.delegation import McpDelegationTokenIssuer
from app.infrastructure.mcp.tool_payloads import _mcp_result_text
from app.infrastructure.mcp.tool_schemas import MCP_TOOL_PAYLOAD_SCHEMAS


class McpToolBindingConfigurationError(ValueError):
    """Clientes MCP não podem ser expostos sem o emissor de delegações exigido."""


class McpToolInput(BaseModel):
    """Formato de transporte dos servidores Argos: um objeto `payload` por tool."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


def _trusted_config(config: RunnableConfig | None) -> Mapping[str, Any]:
    configurable = (config or {}).get("configurable") or {}
    if not isinstance(configurable, Mapping):
        raise ValueError("contexto autenticado da chamada MCP está ausente")
    return configurable


def _delegation_headers(
    *,
    service: str,
    agent: str,
    operation: str,
    config: RunnableConfig | None,
    issuer: McpDelegationTokenIssuer | None,
) -> dict[str, str]:
    if issuer is None:
        raise McpToolBindingConfigurationError(f"emissor de delegação ausente para o MCP {service}")
    context = _trusted_config(config)
    firebase_uid = context.get("argos_firebase_uid")
    property_ids = context.get("argos_authorized_property_ids")
    request_id = context.get("argos_request_id")
    if not firebase_uid or not property_ids or not request_id:
        raise ValueError("identidade e propriedades autorizadas são obrigatórias para a tool MCP")
    return issuer.headers_for_call(
        service=service,
        firebase_uid=firebase_uid,
        authorized_property_ids=property_ids,
        agent=agent,
        operation=operation,
        request_id=request_id,
    )


def _make_tool(
    client: McpToolServerClient,
    *,
    name: str,
    description: str,
    issuer: McpDelegationTokenIssuer | None,
    agent: str | None = None,
    operation: str | None = None,
) -> StructuredTool:
    payload_schema = MCP_TOOL_PAYLOAD_SCHEMAS.get(name)
    args_schema = create_model(
        f"{name.title().replace('_', '')}Input",
        __base__=McpToolInput,
        payload=(payload_schema or dict[str, Any], Field(description=description)),
    )

    async def invoke(
        payload: dict[str, Any] | BaseModel,
        config: RunnableConfig,
    ) -> str:
        if payload_schema is None:
            request_payload = dict(payload)
        else:
            raw_payload = (
                payload.model_dump(mode="python") if isinstance(payload, BaseModel) else payload
            )
            request_payload = payload_schema.model_validate(raw_payload).model_dump(
                mode="json", exclude_none=True
            )
        request_headers: dict[str, str] | None = None
        if agent is not None and operation is not None:
            request_headers = _delegation_headers(
                service=client.service_name,
                agent=agent,
                operation=operation,
                config=config,
                issuer=issuer,
            )
        result = await client.call_tool(
            name,
            {"payload": request_payload},
            request_headers=request_headers,
        )
        return _mcp_result_text(result)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=description,
        args_schema=args_schema,
    )


def _unavailable_tool(name: str, description: str, message: str) -> StructuredTool:
    def unavailable() -> str:
        return message

    return StructuredTool.from_function(
        func=unavailable,
        name=name,
        description=description,
    )
