"""Contrato de avaliação da segurança de uma entrada já sanitizada."""

from enum import StrEnum
from typing import Protocol


class InputRiskDecision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    UNAVAILABLE = "unavailable"


class InputRiskEvaluator(Protocol):
    async def evaluate(self, text: str) -> InputRiskDecision:
        """Avalia risco sem verificar completude ou executar ações."""
        ...
