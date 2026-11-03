"""Adapters HTTP para provedores OpenAI-compatible e consulta de identidade."""

from argos_a2a_agent.adapters.providers.answers import (
    OpenAICompatibleAnswerGenerator,
    ProviderFallbackAnswerGenerator,
)
from argos_a2a_agent.adapters.providers.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    VoyageEmbeddingProvider,
)
from argos_a2a_agent.adapters.providers.simulated import (
    SimulatedAnswerGenerator,
    SimulatedEmbeddingProvider,
)

__all__ = [
    "OpenAICompatibleAnswerGenerator",
    "ProviderFallbackAnswerGenerator",
    "OpenAICompatibleEmbeddingProvider",
    "VoyageEmbeddingProvider",
    "SimulatedAnswerGenerator",
    "SimulatedEmbeddingProvider",
]
