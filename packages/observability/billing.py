"""Leitura de custo do provider e estimativa com tarifas configuradas."""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import Billing

_COST_KEYS = ("reported_cost", "total_cost_usd", "cost_usd", "total_cost", "cost")
_CURRENCY_KEYS = ("currency", "cost_currency", "currency_code")
_MILLION = Decimal("1000000")
_INPUT_RATE_ENV = "MODEL_INPUT_COST_PER_MILLION_TOKENS"
_OUTPUT_RATE_ENV = "MODEL_OUTPUT_COST_PER_MILLION_TOKENS"


def provider_billing(value: Any) -> Billing | None:
    """Extrai apenas custo real e moeda declarados pelo provider.

    Campos genéricos como ``cost`` e ``total_cost`` exigem moeda explícita.
    Campos terminados em ``_usd`` identificam a moeda no próprio nome. Valores
    ausentes, malformados ou sem moeda permanecem desconhecidos; não são
    convertidos em zero nem calculados a partir de tokens.
    """
    for candidate in _candidate_mappings(value):
        nested = candidate.get("billing")
        if isinstance(nested, Mapping):
            billing = _billing_from_mapping(nested)
            if billing is not None:
                return billing
        billing = _billing_from_mapping(candidate)
        if billing is not None:
            return billing
    return None


def configured_rate_billing(
    *, input_tokens: int | None, output_tokens: int | None, embedding: bool = False
) -> Billing | None:
    """Estimate USD cost from globally configured per-million-token rates.

    Chat estimates require both token counts and both rates to avoid silently
    undercounting partial usage. Embeddings are input-only and require only the
    input count and rate. Explicit provider-reported prices should be preferred.
    """
    input_rate = _environment_rate(_INPUT_RATE_ENV)
    if input_rate is None or input_tokens is None:
        return None
    cost = input_rate * input_tokens / _MILLION
    if not embedding:
        output_rate = _environment_rate(_OUTPUT_RATE_ENV)
        if output_rate is None or output_tokens is None:
            return None
        cost += output_rate * output_tokens / _MILLION
    return Billing(
        reported_cost=cost,
        currency="USD",
        cost_source="configured_rate",
    )


def billing_with_fallback(
    value: Any,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    embedding: bool = False,
) -> Billing | None:
    """Return explicit provider cost first, otherwise estimate from env rates."""
    return provider_billing(value) or configured_rate_billing(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        embedding=embedding,
    )


def billing_from_usage_payload(value: Any, *, embedding: bool = False) -> Billing | None:
    """Resolve provider cost or an estimate from common nested token usage shapes."""
    explicit = provider_billing(value)
    if explicit is not None:
        return explicit
    for candidate in _candidate_mappings(value):
        input_tokens = _first_count(
            candidate, ("input_tokens", "prompt_tokens", "prompt_token_count")
        )
        output_tokens = _first_count(
            candidate,
            ("output_tokens", "completion_tokens", "candidates_token_count"),
        )
        if input_tokens is not None:
            estimate = configured_rate_billing(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                embedding=embedding,
            )
            if estimate is not None:
                return estimate
    return None


def langsmith_usage_metadata(
    billing: Billing,
    usage: Mapping[str, Any] | None,
    *,
    embedding: bool = False,
) -> dict[str, Any]:
    """Build LangSmith's recognized usage fields without changing stored billing."""
    source = usage or {}
    allowed_keys = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_token_details",
        "output_token_details",
        "input_cost",
        "output_cost",
        "total_cost",
        "input_cost_details",
        "output_cost_details",
    }
    metadata = {key: value for key, value in source.items() if key in allowed_keys}
    input_details = dict(metadata.get("input_token_details") or {})
    output_details = dict(metadata.get("output_token_details") or {})
    if isinstance(source.get("cache_read_tokens"), int):
        input_details.setdefault("cache_read", source["cache_read_tokens"])
    if isinstance(source.get("cache_write_tokens"), int):
        input_details.setdefault("cache_creation", source["cache_write_tokens"])
    if isinstance(source.get("reasoning_tokens"), int):
        output_details.setdefault("reasoning", source["reasoning_tokens"])
    if input_details:
        metadata["input_token_details"] = input_details
    if output_details:
        metadata["output_token_details"] = output_details
    if billing.currency != "USD":
        return metadata

    metadata["total_cost"] = float(billing.reported_cost)
    if billing.cost_source != "configured_rate":
        return metadata

    input_tokens = _first_count(metadata, ("input_tokens", "prompt_tokens"))
    output_tokens = _first_count(metadata, ("output_tokens", "completion_tokens"))
    input_rate = _environment_rate(_INPUT_RATE_ENV)
    output_rate = _environment_rate(_OUTPUT_RATE_ENV)
    if input_rate is not None and input_tokens is not None:
        metadata["input_cost"] = float(input_rate * input_tokens / _MILLION)
    if not embedding and output_rate is not None and output_tokens is not None:
        metadata["output_cost"] = float(output_rate * output_tokens / _MILLION)
    return metadata


def _environment_rate(name: str) -> Decimal | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation, ValueError:
        return None
    return value if value.is_finite() and value >= 0 else None


def _first_count(value: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        count = value.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
    return None


def _candidate_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _candidate_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _candidate_mappings(child)


def _billing_from_mapping(value: Mapping[str, Any]) -> Billing | None:
    key = next((name for name in _COST_KEYS if name in value), None)
    if key is None:
        return None
    amount = _decimal(value.get(key))
    if amount is None:
        return None
    currency = next(
        (value.get(name) for name in _CURRENCY_KEYS if isinstance(value.get(name), str)),
        None,
    )
    if key.endswith("_usd"):
        currency = "USD"
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        return None
    currency = currency.upper()
    service_tier = value.get("service_tier")
    price_reference = value.get("price_reference") or value.get("price_model_id")
    return Billing(
        reported_cost=amount,
        currency=currency,
        cost_source="provider",
        service_tier=(service_tier[:80] if isinstance(service_tier, str) else None),
        price_reference=(price_reference[:120] if isinstance(price_reference, str) else None),
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    if not result.is_finite() or result < 0:
        return None
    return result
