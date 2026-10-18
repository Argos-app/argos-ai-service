"""Composição opcional do callback LangSmith com ocultação de conteúdo."""

from __future__ import annotations

import logging
from copy import deepcopy
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

        pii_anonymizer = create_anonymizer(
            [
                {
                    "pattern": r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*",
                    "replace": "Bearer [REDACTED]",
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
                {
                    "pattern": r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
                    "replace": "[CNPJ]",
                },
                {
                    "pattern": (
                        r"(?<![A-Za-z0-9])(?:\+?55[\s.-]?)?\(?[1-9]\d\)?[\s.-]?"
                        r"9?\d{4}[\s.-]?\d{4}(?![A-Za-z0-9])"
                    ),
                    "replace": "[TELEFONE]",
                },
            ]
        )

        def anonymizer(data: Any) -> Any:
            """Mask PII while preserving the technical keys used to find a trace."""
            value = deepcopy(data)
            preserved: list[tuple[tuple[Any, ...], str]] = []

            def collect(node: Any, path: tuple[Any, ...] = ()) -> None:
                if isinstance(node, dict):
                    for key, item in node.items():
                        child_path = (*path, key)
                        is_correlation_field = key in {
                            "argos_correlation_id",
                            "argos_correlation_key",
                        }
                        if is_correlation_field and isinstance(item, str):
                            preserved.append((child_path, item))
                        collect(item, child_path)
                elif isinstance(node, list):
                    for index, item in enumerate(node):
                        collect(item, (*path, index))

            collect(value)
            masked = pii_anonymizer(value)
            for path, original in preserved:
                target = masked
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = original
            return masked

        client = Client(
            api_url=settings.langsmith_endpoint,
            api_key=settings.langsmith_api_key,
            workspace_id=settings.langsmith_workspace_id,
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
