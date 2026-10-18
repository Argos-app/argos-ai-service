"""Medição local A2A sem dependência do main_api ou captura de payloads."""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

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
)


def active_collector(collector: ObservationCollector | None) -> ObservationCollector | None:
    if collector is None or isinstance(collector, DisabledObservationCollector):
        return None
    return collector


def runtime_environment() -> str:
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


class OperationRecorder:
    """Emite início e um terminal por operação; a persistência é best-effort."""

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
    ) -> None:
        self._collector = collector
        self._timer = OperationTimer()
        self._error_origin = error_origin
        self._finished = False
        self._started = Observation(
            _id=str(uuid4()),
            service="a2a_agent",
            environment=runtime_environment(),
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
        failure = classify_exception(failure_error, origin=origin) if error else None
        terminal_status = status or (
            failure.status if failure is not None else ObservationStatus.SUCCEEDED
        )
        if failure is not None:
            error_metadata = failure.error
        elif terminal_status in (ObservationStatus.FAILED, ObservationStatus.TIMEOUT):
            error_metadata = classify_exception(
                TimeoutError() if terminal_status == ObservationStatus.TIMEOUT else RuntimeError(),
                origin=origin,
            ).error
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


@asynccontextmanager
async def observe_operation(
    collector: ObservationCollector | None,
    **recorder_options: Any,
) -> AsyncIterator[OperationRecorder | None]:
    recorder = (
        OperationRecorder(active, **recorder_options)
        if (active := active_collector(collector)) is not None
        else None
    )
    try:
        yield recorder
    except BaseException as exc:
        if recorder is not None:
            recorder.finish(error=exc)
        raise
    else:
        if recorder is not None:
            recorder.finish()


class _ObservedAdapter:
    def __init__(
        self,
        target: Any,
        collector: ObservationCollector,
        *,
        operation_prefix: str,
        error_origin: ErrorOrigin,
        operation_kind: OperationKind,
    ) -> None:
        self._target = target
        self._collector = collector
        self._operation_prefix = operation_prefix
        self._error_origin = error_origin
        self._operation_kind = operation_kind

    def __getattr__(self, name: str) -> Any:
        member = getattr(self._target, name)
        valid_name = re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is not None
        if not inspect.iscoroutinefunction(member) or not valid_name:
            return member

        async def observed(*args: Any, **kwargs: Any) -> Any:
            async with observe_operation(
                self._collector,
                operation_kind=self._operation_kind,
                operation_name=f"{self._operation_prefix}.{name}",
                observation_level=ObservationLevel.SDK_INVOCATION,
                operation_phase=OperationPhase.EXECUTION,
                error_origin=self._error_origin,
            ):
                return await member(*args, **kwargs)

        return observed


def observe_adapter(
    target: Any,
    collector: ObservationCollector | None,
    *,
    operation_prefix: str,
    error_origin: ErrorOrigin = ErrorOrigin.STORAGE,
    operation_kind: OperationKind = OperationKind.STORAGE,
) -> Any:
    active = active_collector(collector)
    if active is None:
        return target
    return _ObservedAdapter(
        target,
        active,
        operation_prefix=operation_prefix,
        error_origin=error_origin,
        operation_kind=operation_kind,
    )
