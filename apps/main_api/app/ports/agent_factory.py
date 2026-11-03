"""Contrato para criar instâncias de agentes conhecidos."""

from typing import Any, Protocol

from app.ports.agent import Agent, AgentId


class AgentFactory(Protocol):
    """Cria um agente sem expor a tecnologia usada na implementação."""

    def create(self, agent_id: AgentId) -> Agent[Any, Any]: ...
