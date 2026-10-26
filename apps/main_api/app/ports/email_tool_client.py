"""Porta de chamada às ferramentas MCP usadas pelos fluxos de e-mail."""

from collections.abc import Mapping
from typing import Any, Protocol


class EmailToolClient(Protocol):
    service_name: str

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        request_headers: Mapping[str, str] | None = None,
    ) -> Any: ...
