"""Composição opcional do callback LangSmith com ocultação de conteúdo."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from langchain_core.messages.ai import add_usage

from app.settings import Settings
from packages.observability import billing_from_usage_payload, langsmith_usage_metadata

_LOGGER = logging.getLogger(__name__)


def _usage_metadata_from_outputs(outputs: Any) -> dict[str, Any] | None:
    """Extract and aggregate LangChain's serialized message usage metadata."""
    if not isinstance(outputs, Mapping):
        return None
    generations = outputs.get("generations")
    if not isinstance(generations, (list, tuple)):
        return None

    usage = None
    for batch in generations:
        if not isinstance(batch, (list, tuple)):
            continue
        for generation in batch:
            if not isinstance(generation, Mapping):
                continue
            message = generation.get("message")
            if not isinstance(message, Mapping):
                continue
            kwargs = message.get("kwargs")
            if not isinstance(kwargs, Mapping):
                continue
            message_usage = kwargs.get("usage_metadata")
            if isinstance(message_usage, Mapping):
                usage = add_usage(usage, dict(message_usage))
    return dict(usage) if isinstance(usage, Mapping) else None


def _attach_provider_billing(run: Any, *, billing: Any | None = None) -> None:
    """Attach cost and token usage before LangSmith persists the completed run."""
    outputs = getattr(run, "outputs", None)
    billing = billing or billing_from_usage_payload(outputs)
    if billing is None:
        return

    metadata = {"argos_billing": billing.model_dump(mode="json", exclude_none=True)}
    usage_metadata = _usage_metadata_from_outputs(outputs)
    extra = getattr(run, "extra", None)
    current_metadata = extra.get("metadata") if isinstance(extra, Mapping) else None
    current_usage = (
        current_metadata.get("usage_metadata") if isinstance(current_metadata, Mapping) else None
    )
    if usage_metadata is None and isinstance(current_usage, Mapping):
        usage_metadata = dict(current_usage)
    if usage_metadata is None:
        usage_metadata = {}
    usage_metadata = langsmith_usage_metadata(billing, usage_metadata)

    # RunTree.set validates the LangSmith usage schema and merges metadata. The
    # tracer then sends tokens and cost together in its single terminal patch.
    run.set(metadata=metadata, usage_metadata=usage_metadata)
    if isinstance(outputs, dict):
        outputs["usage_metadata"] = dict(usage_metadata)


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

        class BillingAwareLangChainTracer(LangChainTracer):
            """Persists configured/provider cost with the terminal token usage."""

            def _on_llm_end(self, run: Any) -> None:
                billing = billing_from_usage_payload(getattr(run, "outputs", None))
                if billing is not None:
                    _attach_provider_billing(run, billing=billing)
                    self._update_run_single(run)
                    return
                super()._on_llm_end(run)

        callback = BillingAwareLangChainTracer(
            client=client, project_name=settings.langsmith_project
        )
        return LangSmithTracing(client=client, callback=callback)
    except Exception:
        if client is not None:
            client.close()
        _LOGGER.warning("LangSmith tracing could not be initialized")
        return None
