"""Registra chamadas reais de agentes e tools em formato sanitizado."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from app.domain.sessions import (
    ExecutionEvent,
    ExecutionMessage,
    ExecutionMessageRole,
    ExecutionStatus,
    ExecutionTarget,
)
from app.ports.execution_event_repository import (
    ExecutionEventRepository,
    ExecutionEventWriteError,
)
from app.privacy import minimize_pii
from langchain_core.callbacks import AsyncCallbackHandler

MAX_EVENT_TEXT_LENGTH = 8_000
SECRET_KEY_PATTERN = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:bearer|password|passwd|secret|api[_-]?key|access[_-]?token|token)\b\s*[:= ]\s*)"
    r"([^\s,;]+)"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _safe_value(value: Any, *, key: str | None = None) -> Any:
    if key and SECRET_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "model_dump_json"):
        value = value.model_dump_json()
    if isinstance(value, dict):
        return {str(name): _safe_value(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        sanitized = minimize_pii(value)
        if sanitized.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(sanitized)
            except TypeError, ValueError:
                pass
            else:
                if isinstance(decoded, (dict, list)):
                    return _safe_value(decoded)
        return SECRET_VALUE_PATTERN.sub(r"\1[REDACTED]", sanitized)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    sanitized = minimize_pii(str(value))
    return SECRET_VALUE_PATTERN.sub(r"\1[REDACTED]", sanitized)


def _safe_text(value: Any) -> str:
    try:
        sanitized = json.dumps(
            _safe_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except TypeError, ValueError:
        sanitized = json.dumps("[unavailable]", ensure_ascii=False)
    if len(sanitized) > MAX_EVENT_TEXT_LENGTH:
        sanitized = sanitized[: MAX_EVENT_TEXT_LENGTH - 15] + "…[truncated]"
    return sanitized


def _event_id(invocation_id: UUID, state: ExecutionStatus) -> UUID:
    return uuid5(invocation_id, state.value)


class ExecutionObserver:
    """Produz eventos correlacionados para uma interação do usuário."""

    def __init__(
        self,
        repository: ExecutionEventRepository,
        *,
        session_id: UUID,
        interaction_id: UUID,
    ) -> None:
        self._repository = repository
        self._session_id = session_id
        self._interaction_id = interaction_id

    async def agent_started(self, agent_id: str, input_data: Any) -> UUID:
        invocation_id = uuid4()
        now = _utcnow()
        await self._append(
            ExecutionEvent(
                event_id=_event_id(invocation_id, ExecutionStatus.STARTED),
                invocation_id=invocation_id,
                session_id=self._session_id,
                interaction_id=self._interaction_id,
                target=ExecutionTarget.AGENT,
                target_name=agent_id,
                status=ExecutionStatus.STARTED,
                created_at=now,
                sanitized_input=_safe_text(input_data),
            )
        )
        return invocation_id

    async def agent_finished(
        self,
        invocation_id: UUID,
        agent_id: str,
        *,
        output: Any = None,
        failed: bool = False,
    ) -> None:
        status = ExecutionStatus.FAILED if failed else ExecutionStatus.SUCCEEDED
        await self._append(
            ExecutionEvent(
                event_id=_event_id(invocation_id, status),
                invocation_id=invocation_id,
                session_id=self._session_id,
                interaction_id=self._interaction_id,
                target=ExecutionTarget.AGENT,
                target_name=agent_id,
                status=status,
                created_at=_utcnow(),
                sanitized_output=None if failed else _safe_text(output),
                safe_error_code="agent_execution_failed" if failed else None,
                completed_at=_utcnow(),
            )
        )

    def tool_callback(self) -> AsyncCallbackHandler:
        return _ToolExecutionCallback(self)

    async def _append(self, event: ExecutionEvent) -> None:
        try:
            await self._repository.append(event)
        except Exception as exc:
            raise ExecutionEventWriteError("não foi possível persistir evento de execução") from exc

    async def _append_tool_event(
        self,
        *,
        invocation_id: UUID,
        tool_name: str,
        status: ExecutionStatus,
        started_at: datetime,
        input_text: str,
        output: Any = None,
    ) -> None:
        safe_input = input_text
        messages = [
            ExecutionMessage(
                role=ExecutionMessageRole.ASSISTANT_TOOL_CALL,
                sanitized_content=safe_input,
                tool_name=tool_name,
                tool_call_id=str(invocation_id),
            )
        ]
        if status != ExecutionStatus.STARTED:
            messages.append(
                ExecutionMessage(
                    role=ExecutionMessageRole.TOOL_RESPONSE,
                    sanitized_content=(
                        _safe_text(output)
                        if status == ExecutionStatus.SUCCEEDED
                        else "[tool failed]"
                    ),
                    tool_name=tool_name,
                    tool_call_id=str(invocation_id),
                )
            )
        await self._append(
            ExecutionEvent(
                event_id=_event_id(invocation_id, status),
                invocation_id=invocation_id,
                session_id=self._session_id,
                interaction_id=self._interaction_id,
                target=ExecutionTarget.TOOL,
                target_name=tool_name,
                status=status,
                created_at=started_at,
                sanitized_input=safe_input,
                sanitized_output=(
                    _safe_text(output) if status == ExecutionStatus.SUCCEEDED else None
                ),
                messages=tuple(messages),
                safe_error_code=(
                    "tool_execution_failed" if status == ExecutionStatus.FAILED else None
                ),
                completed_at=None if status == ExecutionStatus.STARTED else _utcnow(),
            )
        )


class _ToolExecutionCallback(AsyncCallbackHandler):
    def __init__(self, observer: ExecutionObserver) -> None:
        super().__init__()
        self.raise_error = True
        self._observer = observer
        self._started: dict[UUID, tuple[str, datetime, str]] = {}

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, kwargs
        tool_name = str(serialized.get("name") or "unknown_tool")
        started_at = _utcnow()
        safe_input = _safe_text(inputs if inputs is not None else input_str)
        self._started[run_id] = (tool_name, started_at, safe_input)
        await self._observer._append_tool_event(
            invocation_id=run_id,
            tool_name=tool_name,
            status=ExecutionStatus.STARTED,
            started_at=started_at,
            input_text=safe_input,
        )

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del kwargs
        started = self._started.pop(run_id, None)
        if started is None:
            return
        tool_name, started_at, input_text = started
        await self._observer._append_tool_event(
            invocation_id=run_id,
            tool_name=tool_name,
            status=ExecutionStatus.SUCCEEDED,
            started_at=started_at,
            input_text=input_text,
            output=output,
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del error, kwargs
        started = self._started.pop(run_id, None)
        if started is None:
            return
        tool_name, started_at, input_text = started
        await self._observer._append_tool_event(
            invocation_id=run_id,
            tool_name=tool_name,
            status=ExecutionStatus.FAILED,
            started_at=started_at,
            input_text=input_text,
        )
