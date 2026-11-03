"""Execução de um especialista e validação das evidências retornadas."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.agent_schemas.inputs import DependencyEvidence, DependencyResult, SpecialistInput
from app.agent_schemas.results import RouteTask, SourceReference, SpecialistResult, SpecialistStatus
from app.agent_schemas.synthesis import TaskOutcome
from app.application.execution_limits import ExecutionLimits, current_execution_limits
from app.application.verified_evidence import verified_evidence_fallback
from app.graph.specialist_helpers import (
    agent_failure_reason,
    build_agent_context,
    history_context,
    unique_sources,
)
from app.graph.state import AgentState
from app.ports.agent import AgentId
from app.ports.agent_factory import AgentFactory
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.execution_event_repository import ExecutionEventWriteError
from langchain_core.runnables import RunnableConfig

_LOGGER = logging.getLogger(__name__)
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."


@dataclass(frozen=True, slots=True)
class SpecialistExecutionResult:
    result: SpecialistResult
    failure_reason: str | None


async def execute_specialist(
    *,
    state: AgentState,
    task: RouteTask,
    dependencies: tuple[TaskOutcome, ...],
    blocked: bool,
    report_source_missing: bool,
    factory: AgentFactory,
    agent_id: AgentId,
    report_refs_by_dependency: dict[str, tuple[str, ...]],
    report_artifact_scopes: dict[str, dict[str, str]],
    email_preparations: EmailPreparationRepository | None,
    config: RunnableConfig | None,
) -> SpecialistExecutionResult:
    """Preserva resultados externos válidos quando somente a interpretação do modelo falhar.
    Cancelamento, limite de execução e erros de armazenamento mantêm seus efeitos originais."""
    failure_reason = None
    if blocked:
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
            session_context=history_context(state),
            email_send_authorized=(
                state.get("send_intent_requested", False)
                if agent_id == AgentId.SECRETARY
                else False
            ),
        )
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
            failure_reason = agent_failure_reason(agent_id.value, exc)
            _LOGGER.warning(
                "falha na execução do agente: %s",
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

    return SpecialistExecutionResult(result, failure_reason)
