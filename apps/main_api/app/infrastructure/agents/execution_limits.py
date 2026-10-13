"""Adaptador LangChain que aplica o orçamento da camada de aplicação."""

from __future__ import annotations

from app.application.execution_limits import (
    ExecutionLimitExceeded,
    ExecutionLimits,
    current_execution_limits,
    execution_limits_scope,
)
from langchain_core.callbacks import AsyncCallbackHandler


class ExecutionLimitCallback(AsyncCallbackHandler):
    """Conta chamadas de modelos e ferramentas durante uma execução LangChain."""

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


__all__ = [
    "ExecutionLimitCallback",
    "ExecutionLimitExceeded",
    "ExecutionLimits",
    "current_execution_limits",
    "execution_limits_scope",
]
