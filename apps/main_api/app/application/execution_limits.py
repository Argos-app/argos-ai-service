"""Orçamento e evidência de execução associados a um caso de uso."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import uuid4


class ExecutionLimitExceeded(RuntimeError):
    """A interação excedeu o orçamento configurado de chamadas."""


@dataclass(slots=True)
class ExecutionLimits:
    max_model_calls: int
    max_tool_calls: int
    max_tool_output_chars: int = 48_000
    model_calls: int = 0
    tool_calls: int = 0
    tool_output_chars: int = 0
    _evidence: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def reserve_model_call(self) -> None:
        if self.model_calls >= self.max_model_calls:
            raise ExecutionLimitExceeded("model_call_budget_exceeded")
        self.model_calls += 1

    def reserve_tool_call(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise ExecutionLimitExceeded("tool_call_budget_exceeded")
        self.tool_calls += 1

    def reserve_tool_output(self, output: object) -> None:
        size = len(output) if isinstance(output, str) else len(repr(output))
        if self.tool_output_chars + size > self.max_tool_output_chars:
            raise ExecutionLimitExceeded("tool_output_budget_exceeded")
        self.tool_output_chars += size

    def record_tool_success(self, agent: str, tool_name: str) -> None:
        """Registra referência opaca, sem persistir o conteúdo da resposta."""
        if tool_name.startswith("mock_") or tool_name.endswith("_unavailable"):
            return
        self._evidence.setdefault(agent, []).append((tool_name, f"tool-call:{uuid4()}"))

    def evidence_for(self, agent: str) -> tuple[tuple[str, str], ...]:
        return tuple(self._evidence.get(agent, ()))


_ACTIVE_LIMITS: ContextVar[ExecutionLimits | None] = ContextVar(
    "argos_active_execution_limits", default=None
)


def current_execution_limits() -> ExecutionLimits | None:
    return _ACTIVE_LIMITS.get()


@contextmanager
def execution_limits_scope(limits: ExecutionLimits) -> Iterator[None]:
    token: Token[ExecutionLimits | None] = _ACTIVE_LIMITS.set(limits)
    try:
        yield
    finally:
        _ACTIVE_LIMITS.reset(token)
