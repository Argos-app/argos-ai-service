"""Transporte HTTP limitado para APIs internas de modelos."""

import json
import re
from typing import Any

import httpx

from argos_a2a_agent.adapters.observability.langsmith import attach_provider_billing
from argos_a2a_agent.adapters.observability.recording import OperationRecorder
from packages.observability import (
    ErrorOrigin,
    HttpMetadata,
    Usage,
    UsageAvailability,
    UsageSource,
    billing_with_fallback,
)


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

    async def post_json(
        self,
        payload: dict[str, Any],
        *,
        recorder: OperationRecorder | None = None,
        operation_route: str = "provider",
        rate_profile: str | None = None,
    ) -> dict[str, Any]:
        status_code: int | None = None
        try:
            async with self._client.stream("POST", self._endpoint, json=payload) as response:
                status_code = response.status_code
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError("resposta do provedor excede 4 MiB")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("resposta do provedor deve ser objeto JSON")
        except BaseException as exc:
            if recorder is not None:
                recorder.finish(
                    error=exc,
                    error_origin=ErrorOrigin.PROVIDER,
                    http=_http_metadata(operation_route, status_code),
                    usage=Usage(availability=UsageAvailability.ABSENT),
                )
            if isinstance(exc, (httpx.HTTPError, ValueError)):
                raise RuntimeError(
                    "provedor HTTP indisponível ou retornou resposta inválida"
                ) from exc
            raise
        is_embedding = operation_route.rstrip("/").endswith("embeddings")
        if recorder is not None:
            usage = _provider_usage(result.get("usage"), embedding=is_embedding)
            billing = billing_with_fallback(
                result,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                embedding=is_embedding,
                rate_profile=rate_profile,
            )
            recorder.finish(
                http=_http_metadata(operation_route, status_code),
                model_returned=_safe_identifier(result.get("model")),
                usage=usage,
                billing=billing,
            )
        else:
            usage = _provider_usage(result.get("usage"), embedding=is_embedding)
            billing = billing_with_fallback(
                result,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                embedding=operation_route.rstrip("/").endswith("embeddings"),
                rate_profile=rate_profile,
            )
        attach_provider_billing(
            billing,
            usage.model_dump(mode="json", exclude_none=True),
            embedding=is_embedding,
            rate_profile=rate_profile,
        )
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def _http_metadata(route: str, status_code: int | None) -> HttpMetadata:
    return HttpMetadata(method="POST", route=route, status_code=status_code)


def _safe_identifier(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value):
        return value
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _provider_usage(value: Any, *, embedding: bool = False) -> Usage:
    if not isinstance(value, dict):
        return Usage(availability=UsageAvailability.ABSENT)
    prompt_details = value.get("prompt_tokens_details")
    completion_details = value.get("completion_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    input_tokens = _count(value.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _count(value.get("prompt_tokens"))
    output_tokens = _count(value.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _count(value.get("completion_tokens"))
    total_tokens = _count(value.get("total_tokens"))
    if embedding and input_tokens is None:
        input_tokens = total_tokens
    cache_read = _count(prompt_details.get("cached_tokens"))
    cache_write = _count(prompt_details.get("cache_creation_tokens"))
    reasoning = _count(completion_details.get("reasoning_tokens"))

    canonical = {
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_creation_tokens",
        "reasoning_tokens",
    }
    details: dict[str, int] = {}
    for key, raw_value in value.items():
        if key in canonical:
            continue
        if isinstance(raw_value, dict):
            for nested_key, nested_value in raw_value.items():
                if nested_key in canonical:
                    continue
                safe_key = _safe_identifier(f"{key}.{nested_key}")
                count = _count(nested_value)
                if safe_key is not None and count is not None and len(details) < 32:
                    details[safe_key] = count
        else:
            safe_key = _safe_identifier(key)
            count = _count(raw_value)
            if safe_key is not None and count is not None and len(details) < 32:
                details[safe_key] = count
    availability = (
        UsageAvailability.RETURNED
        if input_tokens is not None and output_tokens is not None
        else UsageAvailability.PARTIAL
    )
    return Usage(
        availability=availability,
        source=UsageSource.PROVIDER,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        details=details,
    )
