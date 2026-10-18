"""Orçamento e evidência de execução associados a um caso de uso."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from uuid import uuid4


class ExecutionLimitExceeded(RuntimeError):
    """A interação excedeu o orçamento configurado de chamadas."""


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """Trecho sanitizado de uma resposta de ferramenta, mantido só em memória."""

    tool_name: str
    reference: str
    excerpt: str
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class NonEvidenceToolResult:
    """Resultado técnico concluído, não utilizável como evidência factual."""

    tool_name: str
    status: str
    reference: str


@dataclass(slots=True)
class ExecutionLimits:
    max_model_calls: int
    max_tool_calls: int
    max_tool_output_chars: int = 48_000
    model_calls: int = 0
    tool_calls: int = 0
    tool_output_chars: int = 0
    max_evidence_chars: int = 48_000
    _evidence: dict[str, list[ToolEvidence]] = field(default_factory=dict)
    _evidence_sequence: list[ToolEvidence] = field(default_factory=list)
    _non_evidence_results: dict[str, list[NonEvidenceToolResult]] = field(default_factory=dict)
    _evidence_chars: int = 0
    _evidence_incomplete: bool = False

    def reserve_model_call(self) -> None:
        if self.model_calls >= self.max_model_calls:
            raise ExecutionLimitExceeded("model_call_budget_exceeded")
        self.model_calls += 1

    def reserve_tool_call(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise ExecutionLimitExceeded("tool_call_budget_exceeded")
        self.tool_calls += 1

    def reserve_tool_output(self, output: object) -> None:
        size = len(output) if isinstance(output, str) else len(repr(output))
        if self.tool_output_chars + size > self.max_tool_output_chars:
            self._evidence_incomplete = True
            raise ExecutionLimitExceeded("tool_output_budget_exceeded")
        self.tool_output_chars += size

    def record_tool_success(
        self,
        agent: str,
        tool_name: str,
        excerpt: str = "",
        *,
        reference: str | None = None,
    ) -> None:
        """Mantém evidência sanitizada e limitada para a síntese desta interação."""
        result_status = _non_evidence_tool_status(excerpt)
        if result_status is not None:
            self._non_evidence_results.setdefault(agent, []).append(
                NonEvidenceToolResult(
                    tool_name=tool_name,
                    status=result_status,
                    reference=reference or f"tool-call:{uuid4()}",
                )
            )
        if (
            agent == "secretary"
            or tool_name.startswith("mock_")
            or tool_name.endswith("_unavailable")
            or not excerpt.strip()
            or result_status is not None
        ):
            return
        remaining = max(0, self.max_evidence_chars - self._evidence_chars)
        bounded_excerpt = excerpt[:remaining]
        if len(bounded_excerpt) < len(excerpt):
            self._evidence_incomplete = True
        if not bounded_excerpt:
            return
        self._evidence_chars += len(bounded_excerpt)
        evidence = ToolEvidence(
            tool_name=tool_name,
            reference=reference or f"tool-call:{uuid4()}",
            excerpt=bounded_excerpt,
        )
        self._evidence.setdefault(agent, []).append(evidence)
        self._evidence_sequence.append(evidence)

    def evidence_for(self, agent: str) -> tuple[ToolEvidence, ...]:
        return tuple(self._evidence.get(agent, ()))

    def evidence_snapshot(self) -> tuple[ToolEvidence, ...]:
        """Returns all verified tool excerpts in execution order for safe fallback."""
        evidence = tuple(self._evidence_sequence)
        if evidence and self._evidence_incomplete:
            return (*evidence[:-1], replace(evidence[-1], truncated=True))
        return evidence

    def latest_non_evidence_result(self, agent: str) -> NonEvidenceToolResult | None:
        results = self._non_evidence_results.get(agent, ())
        return results[-1] if results else None


_NON_EVIDENCE_STATUSES = frozenset(
    {"abstain", "unavailable", "error", "failed", "rejected", "cancelled", "corpus_updating"}
)


def _non_evidence_tool_status(excerpt: str) -> str | None:
    try:
        value = json.loads(excerpt)
    except TypeError, ValueError:
        return None
    if not isinstance(value, dict):
        return None
    content = value.get("content")
    candidates = [content, value] if isinstance(content, dict) else [value]
    for candidate in candidates:
        status = candidate.get("status")
        if isinstance(status, str) and status.casefold() in _NON_EVIDENCE_STATUSES:
            return status.casefold()
    return None


_ACTIVE_LIMITS: ContextVar[ExecutionLimits | None] = ContextVar(
    "argos_active_execution_limits", default=None
)


def current_execution_limits() -> ExecutionLimits | None:
    return _ACTIVE_LIMITS.get()


@contextmanager
def execution_limits_scope(limits: ExecutionLimits) -> Iterator[None]:
    token: Token[ExecutionLimits | None] = _ACTIVE_LIMITS.set(limits)
    try:
        yield
    finally:
        _ACTIVE_LIMITS.reset(token)
