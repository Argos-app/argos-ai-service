"""Testes isolados do núcleo de medição, sanitização e emissão."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from packages.observability import (
    BestEffortObservationCollector,
    DisabledObservationCollector,
    ErrorOrigin,
    ExecutionMode,
    Observation,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    OperationTimer,
    Usage,
    UsageAvailability,
    allowlisted_value,
    classify_exception,
    sanitize_observation,
)
from packages.observability.testing import InMemoryObservationWriter


class FakeClock:
    def __init__(self, civil: datetime, monotonic: int) -> None:
        self.civil = civil
        self.monotonic = monotonic

    def now_utc(self) -> datetime:
        return self.civil

    def monotonic_ns(self) -> int:
        return self.monotonic


class ReadTimeout(Exception):
    pass


def _observation() -> Observation:
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    return Observation(
        _id=str(uuid4()),
        service="main_api",
        environment="test",
        operation_kind=OperationKind.MODEL,
        operation_name="generate_answer",
        observation_level=ObservationLevel.SDK_INVOCATION,
        execution_mode=ExecutionMode.REAL,
        started_at=now,
        updated_at=now,
        status=ObservationStatus.STARTED,
        usage=Usage(availability=UsageAvailability.ABSENT),
    )


def test_timer_uses_monotonic_duration_and_utc_millisecond_timestamps():
    clock = FakeClock(
        datetime(2026, 10, 12, 9, 0, 0, 123456, tzinfo=timezone(timedelta(hours=-3))),
        1_000_000,
    )
    timer = OperationTimer(clock)
    clock.civil += timedelta(seconds=2, microseconds=333444)
    clock.monotonic += 2_333_444_000

    result = timer.finish()

    assert result.started_at == datetime(2026, 10, 12, 12, 0, 0, 123000, tzinfo=UTC)
    assert result.ended_at == datetime(2026, 10, 12, 12, 0, 2, 456000, tzinfo=UTC)
    assert result.duration_ms == 2333.444
    assert timer.finish() is result


def test_timer_keeps_duration_monotonic_when_civil_clock_moves_backwards():
    start = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(start, 10)
    timer = OperationTimer(clock)
    clock.civil -= timedelta(seconds=1)
    clock.monotonic += 1_000_000

    result = timer.finish()

    assert result.ended_at == start
    assert result.duration_ms == 1.0


def test_timer_rejects_naive_civil_clock():
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, 0), 10)

    with pytest.raises(ValueError, match="timezone"):
        OperationTimer(clock)


@pytest.mark.parametrize(
    ("exception", "status", "safe_message"),
    [
        (
            TimeoutError("api_key=sk-secret someone@example.com"),
            ObservationStatus.TIMEOUT,
            "operation timed out",
        ),
        (ReadTimeout("private response text"), ObservationStatus.TIMEOUT, "operation timed out"),
        (
            asyncio.CancelledError("token=secret"),
            ObservationStatus.CANCELLED,
            "operation cancelled",
        ),
        (
            ConnectionError("password=hunter2"),
            ObservationStatus.FAILED,
            "dependency connection failed",
        ),
        (ValueError("private user data"), ObservationStatus.FAILED, "operation failed"),
    ],
)
def test_exception_classification_never_keeps_original_message(exception, status, safe_message):
    failure = classify_exception(exception, origin=ErrorOrigin.TRANSPORT)

    assert failure.status == status
    assert failure.error.origin == ErrorOrigin.TRANSPORT
    assert failure.error.message_sanitized == safe_message
    assert str(exception) not in failure.error.message_sanitized


def test_allowlist_replaces_unapproved_value_without_retaining_it():
    assert allowlisted_value("known_model", {"known_model"}) == "known_model"
    assert allowlisted_value("user@example.com", {"known_model"}, fallback="unknown") == "unknown"
    assert allowlisted_value(None, {"known_model"}) is None


def test_collector_preserves_functional_path_on_writer_failure_and_reports_safe_code():
    reports: list[str] = []
    writer = InMemoryObservationWriter(error=RuntimeError("secret=do-not-log"))
    collector = BestEffortObservationCollector(writer, report_failure=reports.append)
    observation = _observation()

    collector.emit(observation)

    assert reports == ["writer_error"]
    assert writer.observations == []


def test_collector_sanitizes_untrusted_error_message_before_writer():
    original = _observation()
    end = original.started_at + timedelta(seconds=1)
    observation_data = original.model_dump(by_alias=True)
    observation_data.update(
        ended_at=end,
        duration_ms=1000.0,
        updated_at=end,
        status=ObservationStatus.FAILED,
        error={
            "type": "InternalError",
            "origin": ErrorOrigin.PROVIDER,
            "code": "email=user@example.com",
            "message_sanitized": "token=secret user@example.com",
        },
    )
    observation = Observation.model_validate(observation_data)
    sanitized = sanitize_observation(observation)
    writer = InMemoryObservationWriter()

    BestEffortObservationCollector(writer).emit(observation)

    assert sanitized.error is not None
    assert sanitized.error.type == "operation_error"
    assert sanitized.error.code is None
    assert sanitized.error.message_sanitized is None
    assert writer.observations == [sanitized]


def test_collector_reports_full_queue_and_ignores_diagnostic_failure():
    writer = InMemoryObservationWriter(accept=False)
    collector = BestEffortObservationCollector(
        writer,
        report_failure=lambda _code: (_ for _ in ()).throw(RuntimeError("diagnostic down")),
    )

    collector.emit(_observation())


def test_disabled_collector_is_noop():
    DisabledObservationCollector().emit(_observation())
