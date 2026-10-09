"""Porta para verificar a procedência do payload do MCP de dados."""

from typing import Protocol

from packages.contracts import LotReportEnvelope


class SourceVerifier(Protocol):
    async def verify(self, source: LotReportEnvelope) -> LotReportEnvelope:
        """Valida assinatura, hash, validade e escopo do payload recebido."""
