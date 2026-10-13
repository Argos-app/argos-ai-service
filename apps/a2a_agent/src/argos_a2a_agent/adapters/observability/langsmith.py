"""Contexto opcional LangSmith para traces internos do serviço A2A."""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from argos_a2a_agent.settings import Settings

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LangSmithTracing:
    client: Any
    project: str

    def context(self) -> AbstractContextManager[Any]:
        from langsmith import tracing_context

        return tracing_context(
            enabled=True,
            client=self.client,
            project_name=self.project,
        )

    def close(self) -> None:
        self.client.close()


def create_langsmith_tracing(settings: Settings) -> LangSmithTracing | None:
    if not settings.langsmith_api_key:
        return None
    client = None
    try:
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
        return LangSmithTracing(client=client, project=settings.langsmith_project)
    except Exception:
        if client is not None:
            client.close()
        _LOGGER.warning("LangSmith tracing could not be initialized")
        return None
