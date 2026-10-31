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
_RATE_PROFILES = {
    "fast": (
        "MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS",
        "MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS",
    ),
    "fast_fallback": (
        "MODEL_FAST_FALLBACK_INPUT_COST_PER_MILLION_TOKENS",
        "MODEL_FAST_FALLBACK_OUTPUT_COST_PER_MILLION_TOKENS",
    ),
    "specialist": (
        "MODEL_SPECIALIST_INPUT_COST_PER_MILLION_TOKENS",
        "MODEL_SPECIALIST_OUTPUT_COST_PER_MILLION_TOKENS",
    ),
    "specialist_fallback": (
        "MODEL_SPECIALIST_FALLBACK_INPUT_COST_PER_MILLION_TOKENS",
        "MODEL_SPECIALIST_FALLBACK_OUTPUT_COST_PER_MILLION_TOKENS",
    ),
    "embedding": ("MODEL_EMBEDDING_INPUT_COST_PER_MILLION_TOKENS", None),
    "embedding_fallback": ("MODEL_EMBEDDING_FALLBACK_INPUT_COST_PER_MILLION_TOKENS", None),
}


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
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    embedding: bool = False,
    rate_profile: str | None = None,
) -> Billing | None:
    """Estima o custo em USD com as tarifas configuradas para o papel e provider do modelo.

    Estimativas de chat exigem contagens de tokens e as duas tarifas para evitar
    subestimar o uso parcial. Embeddings cobram somente a entrada e exigem a
    respectiva contagem e tarifa. Prefira preços informados explicitamente pelo provider.
    """
    profile = _RATE_PROFILES.get(rate_profile or "")
    if profile is None:
        return None
    input_rate = _environment_rate(profile[0])
    if input_rate is None or input_tokens is None:
        return None
    cost = input_rate * input_tokens / _MILLION
    if not embedding:
        output_rate = _environment_rate(profile[1]) if profile[1] else None
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
    rate_profile: str | None = None,
) -> Billing | None:
    """Prefere o custo informado pelo provider; se ausente, estima pelas tarifas do ambiente."""
    return provider_billing(value) or configured_rate_billing(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        embedding=embedding,
        rate_profile=rate_profile,
    )


def billing_from_usage_payload(
    value: Any, *, embedding: bool = False, rate_profile: str | None = None
) -> Billing | None:
    """Resolve o custo do provider ou estima o valor a partir de formatos usuais de uso."""
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
                rate_profile=rate_profile,
            )
            if estimate is not None:
                return estimate
    return None


def langsmith_usage_metadata(
    billing: Billing,
    usage: Mapping[str, Any] | None,
    *,
    embedding: bool = False,
    rate_profile: str | None = None,
) -> dict[str, Any]:
    """Monta os campos de uso reconhecidos pelo LangSmith sem alterar a cobrança persistida."""
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
    profile = _RATE_PROFILES.get(rate_profile or "")
    input_rate = _environment_rate(profile[0]) if profile else None
    output_rate = _environment_rate(profile[1]) if profile and profile[1] else None
    if input_rate is not None and input_tokens is not None:
        metadata["input_cost"] = float(input_rate * input_tokens / _MILLION)
    if not embedding and output_rate is not None and output_tokens is not None:
        metadata["output_cost"] = float(output_rate * output_tokens / _MILLION)
    return metadata


def configured_rate_profile(provider: str | None, model: str | None) -> str | None:
    """Resolve o perfil tarifário configurado para uma chamada de modelo rastreada."""
    if not isinstance(provider, str) or not isinstance(model, str):
        return None
    env_profiles = {
        "openai": (
            ("OPENAI_MODEL_FAST", "fast"),
            ("OPENAI_MODEL_SPECIALIST", "specialist"),
            ("OPENAI_EMBEDDINGS_MODEL", "embedding"),
        ),
        "anthropic": (
            ("ANTHROPIC_MODEL_FAST_FALLBACK", "fast_fallback"),
            ("ANTHROPIC_MODEL_SPECIALIST_FALLBACK", "specialist_fallback"),
        ),
        "voyageai": (("VOYAGE_EMBEDDINGS_MODEL", "embedding_fallback"),),
    }
    return next(
        (
            profile
            for env_name, profile in env_profiles.get(provider.casefold(), ())
            if os.getenv(env_name, "").strip() == model
        ),
        None,
    )


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
