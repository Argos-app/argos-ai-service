"""Porta de persistência do manifesto técnico do corpus."""

from typing import Protocol

from argos_a2a_agent.domain.models import CorpusManifest


class ManifestRepository(Protocol):
    async def load(self) -> CorpusManifest | None:
        """Carrega a geração publicada, se houver."""

    async def save(self, manifest: CorpusManifest) -> None:
        """Persiste estado técnico; não altera PDFs da fonte."""
