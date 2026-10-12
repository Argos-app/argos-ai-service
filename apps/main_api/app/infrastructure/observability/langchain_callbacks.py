"""Callbacks LangChain para chamadas reais a agentes e ferramentas."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from packages.observability import (
    ErrorOrigin,
    ExecutionMode,
    ObservationCollector,
    ObservationLevel,
    ObservationStatus,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)

from .recording import OperationRecorder

_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


class ObservabilityToolCallback(AsyncCallbackHandler):
    """Mede cada invocação de tool sem guardar argumentos ou resposta."""

    def __init__(self, collector: ObservationCollector, agent_name: str) -> None:
        super().__init__()
        self._collector = collector
        self._agent_name = agent_name
        self._recorders: dict[UUID, tuple[OperationRecorder, bool]] = {}

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del input_str, kwargs
        name = str(serialized.get("name") or "unknown")
        is_unavailable = name.endswith("_unavailable") or name == "documental_unavailable"
        execution_mode = (
            ExecutionMode.UNAVAILABLE
            if is_unavailable
            else ExecutionMode.MOCK
            if name.startswith("mock_")
            else ExecutionMode.REAL
        )
        operation_name = f"tool.{name}" if _SAFE_TOOL_NAME.fullmatch(name) else "tool.invoke"
        recorder = OperationRecorder(
            self._collector,
            operation_kind=OperationKind.TOOL,
            operation_name=operation_name,
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.SDK_INVOCATION,
            agent=self._agent_name,
            execution_mode=execution_mode,
        )
        self._recorders[run_id] = (recorder, is_unavailable)

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del output, kwargs
        started = self._recorders.pop(run_id, None)
        if started is None:
            return
        recorder, is_unavailable = started
        recorder.finish(
            status=ObservationStatus.REJECTED if is_unavailable else None,
            result=(
                ResultMetadata(source="application", code="dependency_unavailable")
                if is_unavailable
                else None
            ),
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del kwargs
        started = self._recorders.pop(run_id, None)
        if started is None:
            return
        started[0].finish(error=error, error_origin=ErrorOrigin.APPLICATION)
