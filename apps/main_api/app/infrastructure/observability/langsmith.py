"""Composição opcional do callback LangSmith com ocultação de conteúdo."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.settings import Settings

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LangSmithTracing:
    """Cliente e callback compartilhados pelas chamadas LangChain do processo."""

    client: Any
    callback: Any

    def close(self) -> None:
        self.client.close()


def create_langsmith_tracing(settings: Settings) -> LangSmithTracing | None:
    """Habilita traces quando há credencial; falhas de tracing são best effort."""
    if not settings.langsmith_api_key:
        return None
    client = None
    try:
        from langchain_core.tracers import LangChainTracer
        from langsmith import Client
        from langsmith.anonymizer import create_anonymizer

        anonymizer = create_anonymizer(
            [
                {
                    "pattern": r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*",
                    "replace": "Bearer [REDACTED]",
                },
                {
                    "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                    "replace": "[EMAIL]",
                },
                {
                    "pattern": (
                        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
                    ),
                    "replace": "[UUID]",
                },
                {
                    "pattern": r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
                    "replace": "[CPF]",
                },
            ]
        )

        client = Client(
            api_url=settings.langsmith_endpoint,
            api_key=settings.langsmith_api_key,
            hide_inputs=not settings.langsmith_capture_content,
            hide_outputs=not settings.langsmith_capture_content,
            anonymizer=anonymizer,
        )
        callback = LangChainTracer(client=client, project_name=settings.langsmith_project)
        return LangSmithTracing(client=client, callback=callback)
    except Exception:
        if client is not None:
            client.close()
        _LOGGER.warning("LangSmith tracing could not be initialized")
        return None
