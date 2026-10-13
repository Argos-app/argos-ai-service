"""Limites por interação e evidências de chamadas externas bem-sucedidas."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import uuid4

from langchain_core.callbacks import AsyncCallbackHandler


class ExecutionLimitExceeded(RuntimeError):
    """A interação excedeu o orçamento configurado de chamadas."""


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
        """Registra uma referência opaca, sem persistir conteúdo da resposta."""
        if tool_name.startswith("mock_") or tool_name.endswith("_unavailable"):
            return
        evidence = self._evidence.setdefault(agent, [])
        evidence.append((tool_name, f"tool-call:{uuid4()}"))

    def evidence_for(self, agent: str) -> tuple[tuple[str, str], ...]:
        return tuple(self._evidence.get(agent, ()))


class ExecutionLimitCallback(AsyncCallbackHandler):
    """Callback LangChain aplicado a cada chamada de modelo e ferramenta."""

    def __init__(self, limits: ExecutionLimits, agent_id: str) -> None:
        super().__init__()
        self.raise_error = True
        self._limits = limits
        self._agent_id = agent_id
        self._counted_model_runs: set[object] = set()
        self._tool_agents: dict[object, tuple[str, str]] = {}

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        del serialized, prompts, kwargs
        self._count_model(run_id)

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        del serialized, messages, kwargs
        self._count_model(run_id)

    def _count_model(self, run_id):
        if run_id not in self._counted_model_runs:
            self._limits.reserve_model_call()
            self._counted_model_runs.add(run_id)

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        del input_str, kwargs
        self._limits.reserve_tool_call()
        name = str(serialized.get("name") or "unknown")
        self._tool_agents[run_id] = (self._agent_id, name)

    async def on_tool_end(self, output, *, run_id, **kwargs):
        del kwargs
        self._limits.reserve_tool_output(output)
        call = self._tool_agents.pop(run_id, None)
        if call is not None:
            self._limits.record_tool_success(*call)

    async def on_tool_error(self, error, *, run_id, **kwargs):
        del error, kwargs
        self._tool_agents.pop(run_id, None)
