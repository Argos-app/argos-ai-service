"""Reduz erros a categorias e mensagens fixas, sem persistir exceções originais."""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass

from .models import ErrorMetadata, ErrorOrigin, Observation, ObservationStatus


@dataclass(frozen=True, slots=True)
class SafeFailure:
    status: ObservationStatus
    error: ErrorMetadata


_SAFE_FAILURES = {
    "cancelled": ("cancelled_error", "operation cancelled"),
    "timeout": ("timeout_error", "operation timed out"),
    "connection": ("connection_error", "dependency connection failed"),
    "unknown": ("operation_error", "operation failed"),
}
_SAFE_ERROR_TYPES = frozenset(error_type for error_type, _ in _SAFE_FAILURES.values())
_SAFE_ERROR_MESSAGES = frozenset(message for _, message in _SAFE_FAILURES.values())
_SAFE_ERROR_CODES = frozenset(
    {"operation_cancelled", "operation_timeout", "connection_failed", "operation_failed"}
)
_TIMEOUT_EXCEPTION_NAMES = frozenset(
    {
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "DocumentAgentDeadlineError",
    }
)
_CONNECTION_EXCEPTION_NAMES = frozenset({"ConnectError", "NetworkError", "RemoteProtocolError"})


def allowlisted_value(
    value: str | None,
    allowed_values: Collection[str],
    *,
    fallback: str | None = None,
) -> str | None:
    """Retorna somente valores previamente aprovados para um campo categórico."""
    if value is None:
        return fallback
    return value if value in allowed_values else fallback


def classify_exception(
    error: BaseException,
    *,
    origin: ErrorOrigin = ErrorOrigin.UNKNOWN,
) -> SafeFailure:
    """Classifica a falha sem ler str(error), args, traceback ou dados do payload."""
    exception_name = type(error).__name__
    if isinstance(error, asyncio.CancelledError):
        category = "cancelled"
        status = ObservationStatus.CANCELLED
        code = "operation_cancelled"
    elif isinstance(error, TimeoutError) or exception_name in _TIMEOUT_EXCEPTION_NAMES:
        category = "timeout"
        status = ObservationStatus.TIMEOUT
        code = "operation_timeout"
    elif isinstance(error, ConnectionError) or exception_name in _CONNECTION_EXCEPTION_NAMES:
        category = "connection"
        status = ObservationStatus.FAILED
        code = "connection_failed"
    else:
        category = "unknown"
        status = ObservationStatus.FAILED
        code = "operation_failed"

    safe_type, safe_message = _SAFE_FAILURES[category]
    return SafeFailure(
        status=status,
        error=ErrorMetadata(
            type=safe_type,
            origin=origin,
            code=code,
            message_sanitized=safe_message,
        ),
    )


def sanitize_observation(observation: Observation) -> Observation:
    """Retira mensagens e códigos fora do catálogo seguro antes da emissão."""
    if observation.error is None:
        return observation

    error = observation.error
    safe_error = ErrorMetadata(
        type=(error.type if error.type in _SAFE_ERROR_TYPES else "operation_error"),
        origin=error.origin,
        code=(error.code if error.code in _SAFE_ERROR_CODES else None),
        message_sanitized=(
            error.message_sanitized if error.message_sanitized in _SAFE_ERROR_MESSAGES else None
        ),
    )
    return observation.model_copy(update={"error": safe_error})
