"""Transporte HTTP limitado para APIs internas de modelos."""

import json
from typing import Any

import httpx


class OpenAICompatibleHTTP:
    """Cliente sem redirects, proxy de ambiente ou mensagens de erro sensíveis."""

    def __init__(self, endpoint: str, api_key: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5)),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        self._endpoint = endpoint

    async def post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client.stream("POST", self._endpoint, json=payload) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError("resposta do provedor excede 4 MiB")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("resposta do provedor deve ser objeto JSON")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("provedor HTTP indisponível ou retornou resposta inválida") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
