"""Helpers locais para registrar tentativas sem guardar payloads funcionais."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from packages.observability import (
    CancellationState,
    DisabledObservationCollector,
    ErrorOrigin,
    ExecutionMode,
    HttpMetadata,
    Observation,
    ObservationCollector,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    OperationPhase,
    OperationTimer,
    ProtocolMetadata,
    ResultMetadata,
    Usage,
    classify_exception,
    current_correlation_id,
    current_parent_observation_id,
    observation_scope,
)


def _environment() -> str:
    value = os.getenv("APP_ENV", "unknown").strip().casefold()
    return {
        "local": "local",
        "development": "development",
        "dev": "development",
        "test": "test",
        "testing": "test",
        "staging": "staging",
        "production": "production",
        "prod": "production",
    }.get(value, "unknown")


def active_collector(collector: ObservationCollector | None) -> ObservationCollector | None:
    """Evita overhead de medição quando a coleta está explicitamente desabilitada."""
    if collector is None or isinstance(collector, DisabledObservationCollector):
        return None
    return collector


class OperationRecorder:
    """Emite início e um único terminal; erros de coleta nunca escapam."""

    def __init__(
        self,
        collector: ObservationCollector,
        *,
        operation_kind: OperationKind,
        operation_name: str,
        observation_level: ObservationLevel = ObservationLevel.LOCAL_OPERATION,
        operation_phase: OperationPhase | None = None,
        agent: str | None = None,
        execution_mode: ExecutionMode = ExecutionMode.REAL,
        protocol: ProtocolMetadata | None = None,
        timeout_ms: float | None = None,
        error_origin: ErrorOrigin = ErrorOrigin.UNKNOWN,
        provider: str | None = None,
        model_requested: str | None = None,
        correlation_id: UUID | None = None,
        parent_observation_id: UUID | None = None,
    ) -> None:
        self._collector = collector
        self._error_origin = error_origin
        self._timer = OperationTimer()
        self._finished = False
        self._started = Observation(
            _id=str(uuid4()),
            correlation_id=correlation_id or current_correlation_id(),
            parent_observation_id=parent_observation_id or current_parent_observation_id(),
            service="main_api",
            environment=_environment(),
            operation_kind=operation_kind,
            operation_name=operation_name[:120],
            operation_phase=operation_phase,
            observation_level=observation_level,
            agent=agent,
            execution_mode=execution_mode,
            started_at=self._timer.started_at,
            updated_at=self._timer.started_at,
            status=ObservationStatus.STARTED,
            protocol=protocol,
            timeout_ms=timeout_ms,
            provider=provider,
            model_requested=model_requested,
        )
        self._emit(self._started)

    def finish(
        self,
        *,
        error: BaseException | None = None,
        status: ObservationStatus | None = None,
        http: HttpMetadata | None = None,
        protocol: ProtocolMetadata | None = None,
        result: ResultMetadata | None = None,
        replayed_result: bool | None = None,
        error_origin: ErrorOrigin | None = None,
        cancellation_state: CancellationState | None = None,
        model_returned: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        timing = self._timer.finish()
        origin = error_origin or self._error_origin
        failure_error = error
        for _ in range(5):
            cause = getattr(failure_error, "__cause__", None)
            if cause is None:
                break
            failure_error = cause
        failure = (
            classify_exception(failure_error, origin=origin) if failure_error is not None else None
        )
        terminal_status = status or (
            failure.status if failure is not None else ObservationStatus.SUCCEEDED
        )
        if failure is not None:
            error_metadata = failure.error
        elif terminal_status == ObservationStatus.FAILED:
            error_metadata = classify_exception(RuntimeError(), origin=origin).error
        else:
            error_metadata = None
        if cancellation_state is None and terminal_status == ObservationStatus.CANCELLED:
            cancellation_state = CancellationState.OBSERVED
        terminal = self._started.model_copy(
            update={
                "ended_at": timing.ended_at,
                "duration_ms": timing.duration_ms,
                "updated_at": timing.ended_at,
                "status": terminal_status,
                "http": http,
                "protocol": protocol or self._started.protocol,
                "result": result,
                "replayed_result": replayed_result,
                "error": error_metadata,
                "cancellation_state": cancellation_state,
                "model_returned": model_returned,
                "usage": usage,
            }
        )
        self._emit(terminal)

    def _emit(self, observation: Observation) -> None:
        try:
            self._collector.emit(observation)
        except Exception:
            return

    @property
    def observation_id(self) -> UUID:
        return self._started.id


@asynccontextmanager
async def observe_operation(
    collector: ObservationCollector,
    **recorder_options: Any,
) -> AsyncIterator[OperationRecorder]:
    recorder = OperationRecorder(collector, **recorder_options)
    with observation_scope(recorder.observation_id):
        try:
            yield recorder
        except BaseException as exc:
            recorder.finish(error=exc)
            raise
        else:
            recorder.finish()
