"""Contexto opcional LangSmith para traces internos do serviço A2A."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from argos_a2a_agent.settings import Settings
from packages.observability import Billing, correlation_key, langsmith_usage_metadata

_LOGGER = logging.getLogger(__name__)


def attach_provider_billing(
    billing: Billing | None,
    usage: Mapping[str, Any] | None = None,
    *,
    embedding: bool = False,
    rate_profile: str | None = None,
) -> None:
    """Anexa o custo do provider ou uma estimativa por tarifa à execução ativa.

    O envio ao LangSmith é de melhor esforço; a observação Mongo é gravada separadamente."""
    if billing is None:
        return
    try:
        from langsmith.run_helpers import get_current_run_tree

        run_tree = get_current_run_tree()
        if run_tree is None:
            return
        run_tree.set(
            metadata={"argos_billing": billing.model_dump(mode="json", exclude_none=True)},
            usage_metadata=langsmith_usage_metadata(
                billing,
                usage,
                embedding=embedding,
                rate_profile=rate_profile,
            ),
        )
    except Exception:
        _LOGGER.warning("falha ao registrar o custo do provider no LangSmith")


@dataclass(slots=True)
class LangSmithTracing:
    client: Any
    agent_id: str
    agent_environment: str

    def context(self, correlation_id: str | None = None) -> AbstractContextManager[Any]:
        from langsmith import agent_address, tracing_context

        metadata = (
            {
                "argos_correlation_id": correlation_id,
                "argos_correlation_key": correlation_key(correlation_id),
            }
            if correlation_id
            else None
        )
        return tracing_context(
            enabled=True,
            client=self.client,
            address=agent_address(self.agent_id, self.agent_environment),
            metadata=metadata,
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
            """Oculta dados pessoais e preserva as chaves técnicas usadas para localizar um trace."""
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
        return LangSmithTracing(
            client=client,
            agent_id=settings.langsmith_agent_id,
            agent_environment=settings.langsmith_agent_environment,
        )
    except Exception:
        if client is not None:
            client.close()
        _LOGGER.warning("não foi possível inicializar o rastreamento do LangSmith")
        return None
