"""Captura duração e uso bruto de cada chamada efetiva ao modelo LangChain."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    Usage,
    UsageAvailability,
    UsageSource,
)

from .recording import OperationRecorder

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
_PROVIDER_MODULES = {
    "openai": "openai",
    "azure": "azure_openai",
    "anthropic": "anthropic",
    "google": "google",
    "vertexai": "google_vertex",
    "bedrock": "aws_bedrock",
    "mistralai": "mistral",
    "cohere": "cohere",
}


def _safe_identifier(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return None


def _model_identity(
    serialized: Mapping[str, Any], callback_kwargs: Mapping[str, Any]
) -> tuple[str, str | None]:
    raw_id = serialized.get("id")
    path = ".".join(str(part) for part in raw_id) if isinstance(raw_id, (list, tuple)) else ""
    lowered_path = path.casefold()
    provider = next(
        (safe for key, safe in _PROVIDER_MODULES.items() if key in lowered_path),
        "unknown",
    )
    serialized_kwargs = serialized.get("kwargs")
    serialized_kwargs = serialized_kwargs if isinstance(serialized_kwargs, Mapping) else {}
    invocation_kwargs = callback_kwargs.get("invocation_params")
    invocation_kwargs = invocation_kwargs if isinstance(invocation_kwargs, Mapping) else {}
    requested = None
    for source in (serialized_kwargs, invocation_kwargs):
        requested = next(
            (
                _safe_identifier(source.get(key))
                for key in ("model", "model_name", "model_id", "deployment_name")
                if _safe_identifier(source.get(key)) is not None
            ),
            None,
        )
        if requested is not None:
            break
    return provider, requested


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _first_count(sources: tuple[Mapping[str, Any], ...], keys: tuple[str, ...]) -> int | None:
    for source in sources:
        for key in keys:
            count = _integer(source.get(key))
            if count is not None:
                return count
    return None


def _flatten_counts(value: Mapping[str, Any], prefix: str = "") -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, item in value.items():
        safe_key = _safe_identifier(str(key))
        if safe_key is None:
            continue
        name = f"{prefix}.{safe_key}" if prefix else safe_key
        nested = _mapping(item)
        if nested is not None:
            for nested_name, nested_count in _flatten_counts(nested, name).items():
                if len(counts) >= 32:
                    break
                counts[nested_name] = nested_count
        else:
            count = _integer(item)
            if count is not None:
                counts[name[:120]] = count
        if len(counts) >= 32:
            break
    return counts


def _usage_from_message(message: Any, llm_output: Any) -> Usage:
    normalized = _mapping(getattr(message, "usage_metadata", None))
    output = _mapping(llm_output)
    raw_usage = None
    if output is not None:
        raw_usage = _mapping(output.get("token_usage")) or _mapping(output.get("usage"))
    response_metadata = _mapping(getattr(message, "response_metadata", None))
    if raw_usage is None and response_metadata is not None:
        raw_usage = _mapping(response_metadata.get("token_usage")) or _mapping(
            response_metadata.get("usage")
        )

    source_mapping = normalized or raw_usage
    if source_mapping is None:
        return Usage(availability=UsageAvailability.ABSENT)

    input_tokens = _first_count(
        (source_mapping,), ("input_tokens", "prompt_tokens", "prompt_token_count")
    )
    output_tokens = _first_count(
        (source_mapping,), ("output_tokens", "completion_tokens", "candidates_token_count")
    )
    total_tokens = _first_count((source_mapping,), ("total_tokens", "total_token_count"))

    input_details = tuple(
        details
        for key in ("input_token_details", "prompt_tokens_details")
        if (details := _mapping(source_mapping.get(key))) is not None
    )
    output_details = tuple(
        details
        for key in ("output_token_details", "completion_tokens_details")
        if (details := _mapping(source_mapping.get(key))) is not None
    )
    cache_read = _first_count(
        (source_mapping, *input_details),
        (
            "cache_read_tokens",
            "cache_read_input_tokens",
            "cached_tokens",
            "cachedContentTokenCount",
            "cache_read",
        ),
    )
    cache_write = _first_count(
        (source_mapping, *input_details),
        ("cache_write_tokens", "cache_creation_input_tokens", "cache_creation", "cache_write"),
    )
    reasoning = _first_count((source_mapping, *output_details), ("reasoning_tokens", "reasoning"))

    canonical_keys = {
        "input_tokens",
        "prompt_tokens",
        "prompt_token_count",
        "output_tokens",
        "completion_tokens",
        "candidates_token_count",
        "total_tokens",
        "total_token_count",
        "cache_read_tokens",
        "cache_read_input_tokens",
        "cached_tokens",
        "cachedContentTokenCount",
        "cache_read",
        "cache_write_tokens",
        "cache_creation_input_tokens",
        "cache_creation",
        "cache_write",
        "reasoning_tokens",
        "reasoning",
    }
    details: dict[str, int] = {}
    for key, value in _flatten_counts(source_mapping).items():
        if key not in canonical_keys and len(details) < 32:
            details[key] = value

    availability = (
        UsageAvailability.RETURNED
        if input_tokens is not None and output_tokens is not None
        else UsageAvailability.PARTIAL
    )
    return Usage(
        availability=availability,
        source=UsageSource.SDK if normalized is not None else UsageSource.PROVIDER,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        details=details,
    )


def _returned_model(response: Any, message: Any) -> str | None:
    metadata = _mapping(getattr(message, "response_metadata", None)) or {}
    llm_output = _mapping(getattr(response, "llm_output", None)) or {}
    for source in (metadata, llm_output):
        for key in ("model_name", "model", "model_id"):
            value = _safe_identifier(source.get(key))
            if value is not None:
                return value
    return None


class ModelUsageCallback(AsyncCallbackHandler):
    """Um documento por chamada do provider, sem ler prompts ou respostas."""

    def __init__(
        self,
        collector: ObservationCollector,
        *,
        agent: str | None = None,
    ) -> None:
        super().__init__()
        self._collector = collector
        self._agent = agent
        self._recorders: dict[UUID, OperationRecorder] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del messages
        self._start(serialized, run_id, kwargs)

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del prompts
        self._start(serialized, run_id, kwargs)

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        del kwargs
        recorder = self._recorders.pop(run_id, None)
        if recorder is None:
            return
        message = self._first_message(response)
        try:
            recorder.finish(
                model_returned=_returned_model(response, message),
                usage=_usage_from_message(message, getattr(response, "llm_output", None)),
            )
        except Exception:
            recorder.finish(usage=Usage(availability=UsageAvailability.ABSENT))

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del kwargs
        recorder = self._recorders.pop(run_id, None)
        if recorder is not None:
            recorder.finish(
                error=error,
                error_origin=ErrorOrigin.PROVIDER,
                usage=Usage(availability=UsageAvailability.ABSENT),
            )

    def _start(
        self,
        serialized: Mapping[str, Any],
        run_id: UUID,
        callback_kwargs: Mapping[str, Any],
    ) -> None:
        if run_id in self._recorders:
            return
        provider, model = _model_identity(serialized, callback_kwargs)
        self._recorders[run_id] = OperationRecorder(
            self._collector,
            operation_kind=OperationKind.MODEL,
            operation_name="model.invoke",
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.SDK_INVOCATION,
            agent=self._agent,
            provider=provider,
            model_requested=model,
            error_origin=ErrorOrigin.PROVIDER,
        )

    @staticmethod
    def _first_message(response: Any) -> Any:
        generations = getattr(response, "generations", None)
        if not isinstance(generations, (list, tuple)):
            return None
        for group in generations:
            if not isinstance(group, (list, tuple)):
                continue
            for generation in group:
                message = getattr(generation, "message", None)
                if message is not None:
                    return message
        return None
