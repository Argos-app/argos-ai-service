"""Coordenação do nó especialista e seus fluxos de execução."""

from typing import Any

from app.agent_schemas.inputs import QuestionFormattingRequest, QuestionOption
from app.agent_schemas.results import (
    SpecialistStatus,
)
from app.agent_schemas.synthesis import TaskOutcome
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.graph.report_workflow import persist_farm_report_references, resolve_report_dependencies
from app.graph.secretary_workflow import process_secretary_flow
from app.graph.specialist_execution import execute_specialist
from app.graph.specialist_helpers import unique_sources
from app.graph.state import AgentState
from app.ports.agent import AgentId
from app.ports.agent_factory import AgentFactory
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.report_artifact_repository import ReportArtifactRepository
from langchain_core.runnables import RunnableConfig

SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."


async def run_specialist_workflow(
    state: AgentState,
    *,
    factory: AgentFactory,
    agent_id: AgentId,
    report_artifacts: ReportArtifactRepository | None = None,
    email_preparations: EmailPreparationRepository | None = None,
    email_dispatch: EmailDispatchWorkflow | None = None,
    config: RunnableConfig | None = None,
) -> dict:
    tasks = state["ordered_tasks"]
    index = state["current_task_index"]
    if index >= len(tasks) or tasks[index].agent.value != agent_id.value:
        return {
            "final_answer": SAFE_ANSWER,
            "errors": [*state.get("errors", []), "invalid_specialist_route"],
        }

    task = tasks[index]
    completed = {outcome.task.task_id: outcome for outcome in state.get("task_outcomes", ())}
    if any(dep not in completed for dep in task.depends_on):
        return {
            "final_answer": SAFE_ANSWER,
            "errors": [*state.get("errors", []), "missing_dependency_result"],
        }
    dependencies = tuple(completed[dep] for dep in task.depends_on)
    blocked = any(
        outcome.result.status not in (SpecialistStatus.SUCCESS, SpecialistStatus.PARTIAL)
        for outcome in dependencies
    )
    errors = list(state.get("errors", []))
    farm_dependencies = tuple(
        outcome for outcome in dependencies if outcome.task.agent.value == AgentId.FARM.value
    )
    report_resolution = await resolve_report_dependencies(
        state,
        farm_dependencies=(farm_dependencies if agent_id == AgentId.SECRETARY else ()),
        blocked=blocked,
        errors=errors,
        repository=report_artifacts,
        config=config,
    )
    if report_resolution.rewind_update is not None:
        return report_resolution.rewind_update
    blocked = report_resolution.blocked
    errors = list(report_resolution.errors)
    report_refs_by_dependency = report_resolution.refs_by_dependency
    report_artifact_scopes = report_resolution.artifact_scopes
    execution = await execute_specialist(
        state=state,
        task=task,
        dependencies=dependencies,
        blocked=blocked,
        report_source_missing="invalid_or_missing_report_artifact" in errors,
        factory=factory,
        agent_id=agent_id,
        report_refs_by_dependency=report_refs_by_dependency,
        report_artifact_scopes=report_artifact_scopes,
        email_preparations=email_preparations,
        config=config,
    )
    result = execution.result
    failure_reason = execution.failure_reason
    called = list(state.get("called_agents", []))
    if not blocked:
        called.append(agent_id.value)
    elif "blocked_dependency" not in errors:
        errors.append("blocked_dependency")
    if failure_reason and failure_reason not in errors:
        errors.append(failure_reason)
    if result.error == "required_external_evidence_missing":
        errors.append(f"{agent_id.value}_external_evidence_missing")

    secretary_flow = await process_secretary_flow(
        state,
        agent_id=agent_id,
        result=result,
        failure_reason=failure_reason,
        errors=errors,
        email_preparations=email_preparations,
        email_dispatch=email_dispatch,
        config=config,
    )
    result = secretary_flow.result
    failure_reason = secretary_flow.failure_reason
    errors = list(secretary_flow.errors)
    trusted_recipient_question = secretary_flow.recipient_question
    email_preparation_confirmed = secretary_flow.email_preparation_confirmed
    email_action_summary = secretary_flow.email_action_summary
    email_dispatch_evidence = secretary_flow.email_dispatch_evidence

    outcome = TaskOutcome(
        task=task,
        result=result,
        email_dispatch=email_dispatch_evidence,
    )
    if result.status == SpecialistStatus.ABSTAIN:
        errors.append(f"{agent_id.value}_abstained")
    elif result.status == SpecialistStatus.PARTIAL:
        errors.append(f"{agent_id.value}_partial_result")
    elif (
        result.status in (SpecialistStatus.ERROR, SpecialistStatus.UNAVAILABLE)
        and not failure_reason
    ):
        errors.append(f"{agent_id.value}_{result.status.value}")
    sources = unique_sources([*state.get("sources", []), *result.sources])
    update: dict[str, Any] = {
        "called_agents": called,
        "errors": errors,
        "current_task_index": index + 1,
        "task_outcomes": (*state.get("task_outcomes", ()), outcome),
        "sources": sources,
    }
    if email_action_summary is not None:
        update["email_action_summary"] = email_action_summary
    if email_dispatch_evidence is not None:
        update["email_dispatch_evidence"] = email_dispatch_evidence
    if email_preparation_confirmed:
        update["email_preparation_confirmed"] = True
    if agent_id == AgentId.SECRETARY and result.error in {
        "secretary_invalid_output",
        "secretary_execution_failed",
    }:
        update["final_answer"] = (
            "Falha explícita da Secretaria: a execução não terminou com uma resposta válida "
            "e confirmada. "
            + (
                "O MCP de e-mail confirmou a preparação e o Principal a registrou; isso não "
                "confirma que houve envio."
                if email_preparation_confirmed
                else (
                    "A preparação do relatório não foi confirmada; não considere a operação "
                    "concluída."
                )
            )
        )
    if agent_id == AgentId.FARM and report_artifacts is not None:
        artifact_update, artifact_error = await persist_farm_report_references(
            state,
            task_id=task.task_id,
            repository=report_artifacts,
            config=config,
        )
        update.update(artifact_update)
        if artifact_error is not None:
            update["errors"] = [*update["errors"], artifact_error]
    if result.status in (SpecialistStatus.NEEDS_CLARIFICATION, SpecialistStatus.NEEDS_INPUT):
        fields = list(dict.fromkeys(result.missing_fields))
        goal = ", ".join(fields) if fields else task.goal
        update["missing_fields"] = fields
        update["clarification_request"] = QuestionFormattingRequest(
            reason_code="SPECIALIST_NEEDS_INPUT",
            question_goal=f"Esclareça: {goal}.",
        )
        update["pending_task_id"] = task.task_id
    if trusted_recipient_question is not None:
        update["clarification_request"] = QuestionFormattingRequest(
            reason_code=trusted_recipient_question.reason_code,
            input_type=trusted_recipient_question.input_type,
            question_goal=trusted_recipient_question.question_goal,
            options=tuple(
                QuestionOption(option_id=option.option_id, label=option.label)
                for option in trusted_recipient_question.options
            ),
        )
        update["question_id"] = str(trusted_recipient_question.question_id)
        update["clarification_expires_at"] = trusted_recipient_question.expires_at.isoformat()
        update["pending_task_id"] = task.task_id
    return update
