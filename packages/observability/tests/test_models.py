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


def test_started_observation_has_no_end_and_uses_versioned_contract():
    observation = Observation(**_observation())

    assert observation.schema_version == "1"
    assert observation.id
    assert observation.ended_at is None
    assert observation.duration_ms is None
    assert observation.model_dump(by_alias=True)["_id"] == observation.id


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


def test_reported_cost_requires_provider_source_and_currency():
    assert Billing(reported_cost=Decimal("0.0123"), currency="USD", cost_source="provider")

    with pytest.raises(ValidationError, match="custo informado exige moeda"):
        Billing(reported_cost=Decimal("0.0123"))

    with pytest.raises(ValidationError):
        Billing(reported_cost=Decimal("0.0123"), currency="USD", cost_source="estimated")
