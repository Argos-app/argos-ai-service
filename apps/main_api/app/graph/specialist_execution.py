"""Execução de um especialista e validação das evidências retornadas."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.agent_schemas.inputs import DependencyEvidence, DependencyResult, SpecialistInput
from app.agent_schemas.results import SourceReference, SpecialistResult, SpecialistStatus
from app.application.execution_limits import ExecutionLimits, current_execution_limits
from app.application.verified_evidence import verified_evidence_fallback
from app.graph.state import AgentState
from app.graph.workflow import (
    _agent_failure_reason,
    _history_context,
    build_agent_context,
    unique_sources,
)
from app.ports.agent import AgentId
from app.ports.agent_factory import AgentFactory
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.execution_event_repository import ExecutionEventWriteError

_LOGGER = logging.getLogger(__name__)
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."


@dataclass(frozen=True, slots=True)
class SpecialistExecutionResult:
    result: SpecialistResult
    failure_reason: str | None
    called: tuple[str, ...]
    errors: tuple[str, ...]


async def execute_specialist(
    *,
    state: AgentState,
    task: Any,
    dependencies: tuple[Any, ...],
    blocked: bool,
    errors: list[str],
    called: list[str],
    factory: AgentFactory,
    agent_id: AgentId,
    report_refs_by_dependency: dict[str, tuple[str, ...]],
    report_artifact_scopes: dict[str, dict[str, str]],
    email_preparations: EmailPreparationRepository | None,
    config: Any,
) -> SpecialistExecutionResult:
    failure_reason = None
    if blocked:
        report_source_missing = "invalid_or_missing_report_artifact" in errors
        result = SpecialistResult(
            agent=task.agent,
            status=SpecialistStatus.UNAVAILABLE,
            answer=(
                "A consulta do lote não disponibilizou a fonte assinada exigida; "
                "nenhum relatório foi preparado e nenhum e-mail foi enviado."
                if report_source_missing
                else "A tarefa anterior não produziu dados confiáveis para continuar."
            ),
            error=(
                "required_signed_report_source_missing"
                if report_source_missing
                else "dependency_unavailable"
            ),
        )
        errors.append("blocked_dependency")
    else:
        dependency_results = tuple(
            DependencyResult(
                task_id=outcome.task.task_id,
                answer=outcome.result.answer,
                source_references=tuple(source.reference for source in outcome.result.sources),
                source_evidence=tuple(
                    DependencyEvidence(
                        title=source.title,
                        reference=source.reference,
                        excerpt=source.excerpt,
                    )
                    for source in outcome.result.sources
                    if source.excerpt
                ),
                report_artifact_refs=report_refs_by_dependency.get(outcome.task.task_id, ()),
            )
            for outcome in dependencies
        )
        agent_input = SpecialistInput(
            task_id=task.task_id,
            request=state.get("sanitized_message") or None,
            goal=task.goal,
            brief_sanitized=task.brief_sanitized,
            context_refs=task.context_refs,
            depends_on=task.depends_on,
            required_evidence=task.required_evidence,
            dependency_results=dependency_results,
            session_context=_history_context(state),
            email_send_authorized=(
                state.get("send_intent_requested", False)
                if agent_id == AgentId.SECRETARY
                else False
            ),
        )
        called.append(agent_id.value)
        try:
            context = build_agent_context(
                state,
                config=config,
                report_artifact_scopes=report_artifact_scopes,
                recipient_selectors=(
                    state.get("recipient_selectors", {}) if agent_id == AgentId.SECRETARY else None
                ),
            )
            if agent_id == AgentId.SECRETARY and email_preparations is not None:
                preparations = await email_preparations.list_prepared_for_session(
                    principal_user_id=UUID(str(context["argos_user_id"])),
                    session_id=UUID(state["session_id"]),
                )
                active_preparations = tuple(
                    item
                    for item in preparations
                    if item.prepared_report.status.value == "prepared"
                    and item.prepared_report.expires_at > datetime.now(UTC)
                )
                agent_input = agent_input.model_copy(
                    update={"email_preparation_count": len(active_preparations)}
                )
            response = await factory.create(agent_id).run(agent_input, context=context)
            result = SpecialistResult.model_validate(response)
            if result.agent.value != agent_id.value:
                raise ValueError("specialist_agent_mismatch")
            if agent_id in {
                AgentId.FARM,
                AgentId.DOCTOR,
                AgentId.ANALYST,
                AgentId.INFORMATION,
            }:
                limits = context.get("argos_execution_limits")
                own_evidence = (
                    limits.evidence_for(agent_id.value)
                    if isinstance(limits, ExecutionLimits)
                    else ()
                )
                inherited_sources = tuple(
                    source
                    for dependency in dependencies
                    for source in dependency.result.sources
                    if source.excerpt
                )
                if (
                    not own_evidence
                    and not inherited_sources
                    and result.status
                    in {
                        SpecialistStatus.SUCCESS,
                        SpecialistStatus.PARTIAL,
                    }
                ):
                    result = result.model_copy(
                        update={
                            "status": SpecialistStatus.UNAVAILABLE,
                            "answer": SAFE_ANSWER,
                            "sources": (),
                            "error": "required_external_evidence_missing",
                        }
                    )
                    errors.append(f"{agent_id.value}_external_evidence_missing")
                else:
                    source_evidence = tuple(
                        SourceReference(
                            title=f"Consulta externa: {item.tool_name}",
                            reference=item.reference,
                            excerpt=item.excerpt,
                        )
                        for item in own_evidence
                    )
                    result = result.model_copy(
                        update={
                            "sources": tuple(unique_sources([*source_evidence, *inherited_sources]))
                        }
                    )
        except ExecutionEventWriteError:
            raise
        except Exception as exc:
            failure_reason = _agent_failure_reason(agent_id.value, exc)
            _LOGGER.warning(
                "agent execution failed: %s",
                type(exc).__name__,
                extra={"agent": agent_id.value, "failure_reason": failure_reason},
            )
            limits = current_execution_limits()
            tool_result = (
                limits.latest_non_evidence_result(agent_id.value)
                if isinstance(limits, ExecutionLimits)
                else None
            )
            tool_status = tool_result.status if tool_result is not None else None
            fallback_status = (
                SpecialistStatus.ABSTAIN
                if tool_status == "abstain"
                else SpecialistStatus.UNAVAILABLE
                if tool_status in {"unavailable", "corpus_updating"}
                else SpecialistStatus.ERROR
            )
            answer = (
                f"A ferramenta {tool_result.tool_name} retornou status {tool_status}; "
                "não há resultado verificável para apresentar."
                if tool_result is not None
                else ""
            )
            result = SpecialistResult(
                agent=task.agent,
                status=fallback_status,
                answer=answer,
                error=failure_reason,
            )
            # Keep successful external results when only model interpretation failed.
            # Cancellation, budget and storage errors must keep their original semantics.
            evidence = (
                limits.evidence_for(agent_id.value)
                if isinstance(limits, ExecutionLimits)
                and ("_provider_" in failure_reason or failure_reason.endswith("_invalid_output"))
                else ()
            )
            if evidence:
                result = SpecialistResult(
                    agent=task.agent,
                    status=SpecialistStatus.PARTIAL,
                    answer=verified_evidence_fallback(evidence, unavailable_stage="processamento"),
                    sources=tuple(
                        SourceReference(
                            title=f"Consulta externa: {item.tool_name}",
                            reference=item.reference,
                            excerpt=item.excerpt,
                        )
                        for item in evidence
                    ),
                    error=failure_reason,
                )
            errors.append(failure_reason)

    return SpecialistExecutionResult(result, failure_reason, called, errors)
