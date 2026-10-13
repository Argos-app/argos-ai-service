"""Agentes especialistas locais para o corpus global de documentos."""

from argos_a2a_agent.agents.factory import DocumentAgentFactory
from argos_a2a_agent.agents.protocol import DocumentAgent
from argos_a2a_agent.agents.registry import DocumentAgentRegistry
from argos_a2a_agent.agents.specialists import (
    DocumentalAgent,
    LegalAgent,
    MixedDocumentAgent,
)

__all__ = [
    "DocumentAgent",
    "DocumentAgentFactory",
    "DocumentAgentRegistry",
    "DocumentalAgent",
    "LegalAgent",
    "MixedDocumentAgent",
]
