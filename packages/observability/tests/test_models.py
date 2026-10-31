"""Validação do contrato de observabilidade bruta."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.observability import (
    Billing,
    ErrorMetadata,
    ErrorOrigin,
    ExecutionMode,
    Observation,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    Usage,
    UsageAvailability,
    provider_billing,
)


def _timestamps() -> tuple[datetime, datetime]:
    start = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    end = datetime(2026, 10, 12, 12, 0, 1, tzinfo=UTC)
    return start, end


def _observation(**overrides) -> dict:
    started_at, ended_at = _timestamps()
    return {
        "_id": str(uuid4()),
        "service": "main_api",
        "environment": "test",
        "operation_kind": OperationKind.MCP,
        "operation_name": "tools/call",
        "observation_level": ObservationLevel.SDK_INVOCATION,
        "execution_mode": ExecutionMode.REAL,
        "started_at": started_at,
        "updated_at": started_at,
        "status": ObservationStatus.STARTED,
        **overrides,
    }


def test_started_observation_has_no_end_and_uses_current_contract():
    observation = Observation(**_observation())

    assert observation.schema_version == "2"
    assert observation.id
    assert observation.ended_at is None
    assert observation.duration_ms is None
    assert observation.model_dump(by_alias=True)["_id"] == observation.id


def test_observation_rejects_obsolete_schema_version():
    with pytest.raises(ValidationError):
        Observation(**_observation(schema_version="1"))


def test_terminal_success_keeps_individually_measured_duration_in_ms():
    _, ended_at = _timestamps()
    observation = Observation(
        **_observation(
            ended_at=ended_at,
            duration_ms=1000.25,
            updated_at=ended_at,
            status=ObservationStatus.SUCCEEDED,
        )
    )

    assert observation.duration_ms == 1000.25
    assert observation.ended_at == ended_at


def test_failed_attempt_requires_error_metadata_and_preserves_safe_message():
    _, ended_at = _timestamps()
    error = ErrorMetadata(
        type="TimeoutError",
        origin=ErrorOrigin.TRANSPORT,
        message_sanitized="tempo limite excedido",
    )
    observation = Observation(
        **_observation(
            ended_at=ended_at,
            duration_ms=1000.0,
            updated_at=ended_at,
            status=ObservationStatus.TIMEOUT,
            error=error,
        )
    )

    assert observation.error == error

    with pytest.raises(ValidationError):
        Observation(
            **_observation(
                ended_at=ended_at,
                duration_ms=1000.0,
                updated_at=ended_at,
                status=ObservationStatus.FAILED,
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"started_at": datetime(2026, 10, 12, 12, 0, 0)},
        {"started_at": datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone(timedelta(hours=3)))},
        {"duration_ms": 1.0},
        {"unknown_field": "not allowed"},
        {"_id": "not-a-uuid"},
    ],
)
def test_contract_rejects_invalid_timestamps_lifecycle_and_extra_fields(overrides):
    with pytest.raises(ValidationError):
        Observation(**_observation(**overrides))


def test_terminal_status_requires_end_and_duration():
    with pytest.raises(ValidationError, match="estado terminal exige fim e duração"):
        Observation(
            **_observation(
                status=ObservationStatus.SUCCEEDED,
            )
        )


def test_usage_marks_missing_data_without_turning_it_into_zero():
    usage = Usage(availability=UsageAvailability.ABSENT)

    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.model_dump(exclude_none=True) == {"availability": "absent", "details": {}}


def test_usage_rejects_negative_token_counts():
    with pytest.raises(ValidationError):
        Usage(availability=UsageAvailability.RETURNED, input_tokens=-1)


def test_retention_expiration_is_not_part_of_the_observation_document():
    with pytest.raises(ValidationError):
        Observation(**_observation(expires_at=datetime(2026, 11, 12, tzinfo=UTC)))


def test_reported_cost_requires_source_and_currency():
    assert Billing(reported_cost=Decimal("0.0123"), currency="USD", cost_source="provider")

    with pytest.raises(ValidationError, match="custo informado exige moeda"):
        Billing(reported_cost=Decimal("0.0123"))

    assert Billing(reported_cost=Decimal("0.0123"), currency="USD", cost_source="configured_rate")


def test_provider_billing_uses_only_explicit_provider_cost_and_preserves_precision():
    billing = provider_billing({"usage": {"total_cost_usd": "0.0000000000125"}})
    assert billing is not None
    assert billing.reported_cost == Decimal("0.0000000000125")
    assert billing.currency == "USD"
    assert billing.cost_source == "provider"
    assert provider_billing({"usage": {"prompt_tokens": 20, "total_tokens": 20}}) is None
    assert provider_billing({"usage": {"cost": "0.12"}}) is None


def test_configured_rates_estimate_chat_and_embedding_costs(monkeypatch):
    from packages.observability import billing_from_usage_payload

    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    monkeypatch.setenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS", "10")

    chat = billing_from_usage_payload(
        {"usage": {"prompt_tokens": 200, "completion_tokens": 50}}, rate_profile="fast"
    )
    assert chat is not None
    assert chat.reported_cost == Decimal("0.001")
    assert chat.currency == "USD"
    assert chat.cost_source == "configured_rate"

    monkeypatch.setenv("MODEL_EMBEDDING_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    embedding = billing_from_usage_payload(
        {"usage": {"prompt_tokens": 200}}, embedding=True, rate_profile="embedding"
    )
    assert embedding is not None
    assert embedding.reported_cost == Decimal("0.0005")


def test_langsmith_usage_metadata_includes_configured_input_and_output_costs(monkeypatch):
    from packages.observability import (
        billing_from_usage_payload,
        langsmith_usage_metadata,
    )

    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    monkeypatch.setenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS", "10")
    usage = {"input_tokens": 200, "output_tokens": 50, "total_tokens": 250}
    billing = billing_from_usage_payload({"usage": usage}, rate_profile="fast")

    assert billing is not None
    assert langsmith_usage_metadata(billing, usage, rate_profile="fast") == {
        "input_tokens": 200,
        "output_tokens": 50,
        "total_tokens": 250,
        "total_cost": 0.001,
        "input_cost": 0.0005,
        "output_cost": 0.0005,
    }


def test_langsmith_usage_metadata_maps_observation_usage_to_sdk_contract(monkeypatch):
    from packages.observability import configured_rate_billing, langsmith_usage_metadata

    monkeypatch.setenv("MODEL_EMBEDDING_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    billing = configured_rate_billing(
        input_tokens=200, output_tokens=None, embedding=True, rate_profile="embedding"
    )

    assert billing is not None
    assert langsmith_usage_metadata(
        billing,
        {
            "availability": "partial",
            "source": "provider",
            "input_tokens": 200,
            "total_tokens": 200,
            "cache_read_tokens": 20,
            "details": {"provider.extra": 10},
        },
        embedding=True,
        rate_profile="embedding",
    ) == {
        "input_tokens": 200,
        "total_tokens": 200,
        "input_token_details": {"cache_read": 20},
        "total_cost": 0.0005,
        "input_cost": 0.0005,
    }


def test_provider_cost_precedes_configured_rates_and_incomplete_usage_stays_unknown(monkeypatch):
    from packages.observability import billing_from_usage_payload

    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    monkeypatch.setenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS", "10")

    provider = billing_from_usage_payload(
        {
            "usage": {
                "prompt_tokens": 200_000,
                "completion_tokens": 50_000,
                "cost_usd": "0.123",
            }
        },
        rate_profile="fast",
    )
    assert provider is not None
    assert provider.reported_cost == Decimal("0.123")
    assert provider.cost_source == "provider"

    assert billing_from_usage_payload({"usage": {"prompt_tokens": 200}}) is None
    monkeypatch.setenv("MODEL_EMBEDDING_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    assert (
        billing_from_usage_payload(
            {"usage": {"prompt_tokens": 200}}, embedding=True, rate_profile="embedding"
        )
        is not None
    )


def test_configured_rates_ignore_invalid_or_missing_values(monkeypatch):
    from packages.observability import configured_rate_billing

    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "-1")
    monkeypatch.setenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS", "10")
    assert configured_rate_billing(input_tokens=10, output_tokens=10, rate_profile="fast") is None

    monkeypatch.setenv("MODEL_FAST_INPUT_COST_PER_MILLION_TOKENS", "2")
    monkeypatch.delenv("MODEL_FAST_OUTPUT_COST_PER_MILLION_TOKENS")
    assert configured_rate_billing(input_tokens=10, output_tokens=10, rate_profile="fast") is None


def test_observation_accepts_direct_interaction_and_trace_ids():
    from uuid import uuid4

    correlation_id = uuid4()
    parent_observation_id = uuid4()
    session_id = uuid4()
    interaction_id = uuid4()
    observation = Observation(
        **_observation(),
        correlation_id=correlation_id,
        parent_observation_id=parent_observation_id,
        session_id=session_id,
        interaction_id=interaction_id,
    )

    assert observation.correlation_id == correlation_id
    assert observation.parent_observation_id == parent_observation_id
    assert observation.session_id == session_id
    assert observation.interaction_id == interaction_id


def test_correlation_context_restores_nested_parent_ids():
    from uuid import uuid4

    from packages.observability import (
        bind_correlation,
        bind_interaction,
        current_correlation_id,
        current_interaction_id,
        current_parent_observation_id,
        current_session_id,
        observation_scope,
    )

    correlation_id = uuid4()
    parent = uuid4()
    child = uuid4()
    session_id = uuid4()
    interaction_id = uuid4()
    assert current_correlation_id() is None
    with bind_correlation(correlation_id):
        assert current_correlation_id() == correlation_id
        with bind_interaction(session_id, interaction_id):
            assert current_session_id() == session_id
            assert current_interaction_id() == interaction_id
        with observation_scope(parent):
            assert current_parent_observation_id() == parent
            with observation_scope(child):
                assert current_parent_observation_id() == child
            assert current_parent_observation_id() == parent
        assert current_parent_observation_id() is None
    assert current_correlation_id() is None
    assert current_session_id() is None
    assert current_interaction_id() is None
