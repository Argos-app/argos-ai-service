"""Contratos estáveis para identificar e executar agentes."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, TypeVar


class AgentId(StrEnum):
    """Conjunto fechado de agentes conhecidos pela aplicação."""

    ROUTER = "router"
    FARM = "farm"
    DOCTOR = "doctor"
    ANALYST = "analyst"
    SECRETARY = "secretary"
    INFORMATION = "information"
    ORCHESTRATOR = "orchestrator"
    JUDGE = "judge"
    QUESTION_FORMATTER = "question_formatter"


InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)


class Agent(Protocol[InputT, OutputT]):
    """Interface assíncrona para executar um agente tipado."""

    async def run(
        self,
        input: InputT,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> OutputT: ...
