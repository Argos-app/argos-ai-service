"""Adapters locais para fontes e estado técnico do corpus documental."""

from argos_a2a_agent.adapters.storage.local_documents import LocalDocumentSource
from argos_a2a_agent.adapters.storage.local_manifest import LocalManifestRepository

__all__ = ["LocalDocumentSource", "LocalManifestRepository"]
