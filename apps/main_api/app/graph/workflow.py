import re
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from app.agent_schemas.inputs import (
    DependencyResult,
    QuestionFormatterInput,
    QuestionFormattingRequest,
    QuestionOption,
    RouterInput,
    SessionContext,
    SpecialistInput,
)
from app.agent_schemas.results import (
    JudgeDecision,
    JudgeResult,
    RoutePlan,
    RouteTask,
    SourceReference,
    SpecialistResult,
    SpecialistStatus,
)
from app.agent_schemas.synthesis import (
    JudgeInput,
    OrchestratorInput,
    OrchestratorResult,
    TaskOutcome,
)
from app.application.report_artifact_service import (
    ReportSourceExpiredError,
    ReportSourceValidationError,
    validate_report_source,
)
from app.domain.email_preparations import PendingRecipientResolution
from app.domain.report_artifacts import (
    ReportArtifactExpiredError,
    ReportArtifactNotFoundError,
)
from app.graph.state import AgentState
from app.ports.agent import AgentId
from app.ports.agent_factory import AgentFactory
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.execution_event_repository import ExecutionEventWriteError
from app.ports.report_artifact_repository import ReportArtifactRepository
from app.privacy import merge_recipient_selectors, minimize_pii, protect_user_input
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

MAX_TASKS = 5
MAX_REVISIONS = 1
MAX_MESSAGE_LENGTH = 8000
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."

SECRET_REQUEST_PATTERN = re.compile(
    r"\b(?:revele|mostre|exiba)\b.{0,60}"
    r"\b(?:chave de api|token de acesso|senha|prompt de sistema)\b",
    re.IGNORECASE,
)


def build_agent_context(
    state: AgentState,
    *,
    config: RunnableConfig | None = None,
    report_artifact_scopes: dict[str, dict[str, str]] | None = None,
    recipient_selectors: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    context = {
        "user_id": state["user_id"],
        "farm_id": state["farm_id"],
        "session_id": state["session_id"],
    }
    if state.get("interaction_id"):
        context["interaction_id"] = state["interaction_id"]
    task_index = state.get("current_task_index", 0)
    tasks = state.get("ordered_tasks", ())
    if 0 <= task_index < len(tasks):
        context["argos_task_id"] = tasks[task_index].task_id
    configurable = (config or {}).get("configurable") or {}
    if configurable.get("argos_user_id"):
        context["argos_user_id"] = configurable["argos_user_id"]
    if configurable.get("argos_authorized_property_ids"):
        context["argos_authorized_property_ids"] = configurable["argos_authorized_property_ids"]
    if configurable.get("argos_request_id"):
        context["argos_request_id"] = configurable["argos_request_id"]
    if report_artifact_scopes:
        context["argos_report_artifact_scopes"] = report_artifact_scopes
    if recipient_selectors is not None:
        context["recipient_selectors"] = recipient_selectors
    return context


def _history_context(state: AgentState) -> SessionContext:
    context = state.get("session_context") or SessionContext()
    return SessionContext(
        session_summary=context.session_summary,
        recent_messages=context.recent_messages,
    )


def _preference_context(state: AgentState) -> SessionContext:
    context = state.get("session_context") or SessionContext()
    return SessionContext(
        user_tone=context.user_tone,
        user_general_goal=context.user_general_goal,
    )


def reset_turn_state() -> dict:
    return {
        "pii_map": {},
        "recipient_selectors": {},
        "send_intent_requested": False,
        "send_intent_source_interaction_id": None,
        "sanitized_message": "",
        "route": [],
        "route_plan": None,
        "ordered_tasks": (),
        "current_task_index": 0,
        "called_agents": [],
        "task_outcomes": (),
        "sources": [],
        "report_artifact_refs": {},
        "report_artifact_origin_interaction_id": None,
        "report_source_renewal_count": 0,
        "missing_fields": [],
        "clarification_request": None,
        "clarification_question": None,
        "question_id": None,
        "clarification_expires_at": None,
        "pending_task_id": None,
        "resume_target": None,
        "draft_result": None,
        "judge_result": None,
        "final_answer": None,
        "errors": [],
        "retry_count": 0,
    }


def reject_input(state: AgentState, answer: str, error: str) -> dict:
    if state.get("input_kind") == "answer" and state.get("question_id"):
        return {
            "final_answer": answer,
            "errors": [*state.get("errors", []), error],
        }
    return {**reset_turn_state(), "final_answer": answer, "errors": [error]}


def initial_state(
    message: str,
    *,
    user_id: str,
    farm_id: str,
    session_id: str,
    interaction_id: str | None = None,
    session_context: SessionContext | None = None,
    recipient_selectors: dict[str, dict[str, str]] | None = None,
    send_intent_requested: bool = False,
) -> dict:
    protected = protect_user_input(message)
    protected_recipients, sanitized_message = merge_recipient_selectors(
        dict(recipient_selectors or {}),
        protected.recipient_selectors,
        protected.sanitized_text,
    )
    return {
        "messages": [HumanMessage(content=sanitized_message)],
        "user_id": user_id,
        "farm_id": farm_id,
        "session_id": session_id,
        "interaction_id": interaction_id,
        "input_kind": "message",
        "answer_question_id": None,
        "session_context": session_context or SessionContext(),
        "pii_map": {},
        "report_artifact_refs": {},
        "report_artifact_origin_interaction_id": interaction_id,
        "report_source_renewal_count": 0,
        "recipient_selectors": protected_recipients,
        "send_intent_requested": send_intent_requested or protected.send_intent_requested,
        "send_intent_source_interaction_id": (
            interaction_id if send_intent_requested or protected.send_intent_requested else None
        ),
    }


def answer_state(
    answer: str,
    *,
    question_id: str,
    interaction_id: str | None = None,
    session_context: SessionContext | None = None,
    recipient_selectors: dict[str, dict[str, str]] | None = None,
    send_intent_requested: bool = False,
    send_intent_source_interaction_id: str | None = None,
) -> dict:
    protected = protect_user_input(answer)
    protected_recipients, sanitized_answer = merge_recipient_selectors(
        dict(recipient_selectors or {}),
        protected.recipient_selectors,
        protected.sanitized_text,
    )
    return {
        "messages": [HumanMessage(content=sanitized_answer)],
        "input_kind": "answer",
        "answer_question_id": question_id,
        "interaction_id": interaction_id,
        "session_context": session_context or SessionContext(),
        "recipient_selectors": protected_recipients,
        "send_intent_requested": send_intent_requested,
        "send_intent_source_interaction_id": send_intent_source_interaction_id,
    }


def order_tasks(plan: RoutePlan) -> tuple[RouteTask, ...]:
    if len(plan.tasks) > MAX_TASKS:
        raise ValueError("too_many_tasks")
    ids = [task.task_id for task in plan.tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_task_id")
    known_ids = set(ids)
    if any(not set(task.depends_on) <= known_ids for task in plan.tasks):
        raise ValueError("unknown_dependency")

    pending = list(plan.tasks)
    ordered: list[RouteTask] = []
    completed: set[str] = set()
    while pending:
        ready = next((task for task in pending if set(task.depends_on) <= completed), None)
        if ready is None:
            raise ValueError("cyclic_dependency")
        ordered.append(ready)
        completed.add(ready.task_id)
        pending.remove(ready)
    return tuple(ordered)


def unique_sources(sources: list[SourceReference]) -> list[SourceReference]:
    unique: list[SourceReference] = []
    for source in sources:
        if source not in unique:
            unique.append(source)
    return unique


def no_guardrail_entrada(state: AgentState) -> dict:
    messages = state.get("messages", [])
    message = messages[-1] if messages else None
    if not isinstance(message, HumanMessage) or not isinstance(message.content, str):
        return reject_input(state, SAFE_ANSWER, "invalid_input")

    raw_text = message.content.strip()
    if not raw_text or len(raw_text) > MAX_MESSAGE_LENGTH:
        return reject_input(
            state, "Envie uma pergunta válida com até 8000 caracteres.", "invalid_input"
        )
    if SECRET_REQUEST_PATTERN.search(raw_text):
        return reject_input(
            state,
            "Não posso fornecer credenciais ou instruções internas do sistema.",
            "blocked_secret_request",
        )

    protected = protect_user_input(raw_text)
    recipient_selectors, sanitized = merge_recipient_selectors(
        dict(state.get("recipient_selectors") or {}),
        protected.recipient_selectors,
        protected.sanitized_text,
    )
    send_intent_requested = bool(state.get("send_intent_requested")) or (
        state.get("input_kind") != "answer" and protected.send_intent_requested
    )
    send_intent_source_interaction_id = state.get("send_intent_source_interaction_id")
    if (
        send_intent_source_interaction_id is None
        and state.get("input_kind") != "answer"
        and send_intent_requested
    ):
        send_intent_source_interaction_id = state.get("interaction_id")
    replacement = [HumanMessage(content=sanitized)]
    if message.id:
        replacement.insert(0, RemoveMessage(id=message.id))

    if state.get("input_kind") != "answer":
        return {
            **reset_turn_state(),
            "sanitized_message": sanitized,
            "recipient_selectors": recipient_selectors,
            "send_intent_requested": send_intent_requested,
            "send_intent_source_interaction_id": send_intent_source_interaction_id,
            "messages": replacement,
        }

    question_id = state.get("question_id")
    if (
        not question_id
        or state.get("answer_question_id") != question_id
        or state.get("clarification_request") is None
    ):
        return reject_input(
            state, "Essa resposta não corresponde à pergunta pendente.", "invalid_question_answer"
        )

    resumed_message = f"{state['sanitized_message']}\nEsclarecimento: {sanitized}"
    pending_task_id = state.get("pending_task_id")
    if pending_task_id is None:
        return {
            **reset_turn_state(),
            "called_agents": list(state.get("called_agents", [])),
            "errors": list(state.get("errors", [])),
            "sanitized_message": resumed_message,
            "recipient_selectors": recipient_selectors,
            "send_intent_requested": send_intent_requested,
            "send_intent_source_interaction_id": send_intent_source_interaction_id,
            "resume_target": "router",
            "messages": replacement,
        }

    tasks = state.get("ordered_tasks", ())
    matching = [index for index, task in enumerate(tasks) if task.task_id == pending_task_id]
    if len(matching) != 1:
        return {
            **reset_turn_state(),
            "called_agents": list(state.get("called_agents", [])),
            "final_answer": SAFE_ANSWER,
            "recipient_selectors": recipient_selectors,
            "send_intent_requested": send_intent_requested,
            "send_intent_source_interaction_id": send_intent_source_interaction_id,
            "errors": [*state.get("errors", []), "missing_pending_task"],
        }

    index = matching[0]
    task = tasks[index]
    updated_task = task.model_copy(
        update={
            "brief_sanitized": f"{task.brief_sanitized}\nEsclarecimento do usuário: {sanitized}"
        }
    )
    updated_tasks = (*tasks[:index], updated_task, *tasks[index + 1 :])
    prior_outcomes = tuple(
        outcome
        for outcome in state.get("task_outcomes", ())
        if outcome.task.task_id != pending_task_id
    )
    prior_sources = unique_sources(
        [source for outcome in prior_outcomes for source in outcome.result.sources]
    )
    return {
        **reset_turn_state(),
        "called_agents": list(state.get("called_agents", [])),
        "errors": list(state.get("errors", [])),
        "sanitized_message": resumed_message,
        "recipient_selectors": recipient_selectors,
        "send_intent_requested": send_intent_requested,
        "send_intent_source_interaction_id": send_intent_source_interaction_id,
        "route_plan": state.get("route_plan"),
        "ordered_tasks": updated_tasks,
        "current_task_index": index,
        "task_outcomes": prior_outcomes,
        "sources": prior_sources,
        "report_artifact_refs": {
            task_id: references
            for task_id, references in state.get("report_artifact_refs", {}).items()
            if task_id in {outcome.task.task_id for outcome in prior_outcomes}
        },
        "report_artifact_origin_interaction_id": state.get("report_artifact_origin_interaction_id"),
        "report_source_renewal_count": state.get("report_source_renewal_count", 0),
        "route": state.get("route", []),
        "resume_target": updated_task.agent.value,
        "messages": replacement,
    }


async def no_roteador(
    state: AgentState,
    *,
    factory: AgentFactory,
    config: RunnableConfig | None = None,
) -> dict:
    called = [*state.get("called_agents", []), AgentId.ROUTER.value]
    try:
        response = await factory.create(AgentId.ROUTER).run(
            RouterInput(
                message=state["sanitized_message"],
                session_context=_history_context(state),
            ),
            context=build_agent_context(state, config=config),
        )
        plan = RoutePlan.model_validate(response)
        ordered = order_tasks(plan)
    except ExecutionEventWriteError:
        raise
    except Exception:
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), "router_failed_or_invalid_plan"],
            "final_answer": SAFE_ANSWER,
        }

    update: dict[str, Any] = {
        "called_agents": called,
        "route_plan": plan,
        "ordered_tasks": ordered,
        "route": [task.agent.value for task in ordered],
    }
    if plan.missing_slots:
        fields = list(dict.fromkeys(plan.missing_slots))
        update["missing_fields"] = fields
        update["clarification_request"] = QuestionFormattingRequest(
            reason_code="MISSING_FIELDS",
            input_type="free_text",
            question_goal=f"Informe os dados necessários: {', '.join(fields)}.",
        )
    elif not ordered:
        update["final_answer"] = (
            "Não consegui identificar uma solicitação que os agentes do Argos possam atender."
        )
    return update


async def no_especialista(
    state: AgentState,
    *,
    factory: AgentFactory,
    agent_id: AgentId,
    report_artifacts: ReportArtifactRepository | None = None,
    email_preparations: EmailPreparationRepository | None = None,
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
    trusted_recipient_question: PendingRecipientResolution | None = None
    report_refs_by_dependency: dict[str, tuple[str, ...]] = {}
    report_artifact_scopes: dict[str, dict[str, str]] = {}
    farm_dependencies = tuple(
        outcome for outcome in dependencies if outcome.task.agent.value == AgentId.FARM.value
    )
    if task.agent.value == AgentId.SECRETARY.value and farm_dependencies and not blocked:
        configurable = (config or {}).get("configurable") or {}
        try:
            principal_user_id = UUID(str(configurable["argos_user_id"]))
            authorized_property_ids = tuple(
                UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
            )
            origin_interaction_id = UUID(
                str(
                    state.get("report_artifact_origin_interaction_id")
                    or state.get("interaction_id")
                )
            )
            session_id = UUID(state["session_id"])
            if principal_user_id != UUID(state["user_id"]):
                raise ValueError("principal_mismatch")
            if report_artifacts is None:
                raise ReportArtifactNotFoundError("artifact_store_unavailable")
            for dependency in farm_dependencies:
                refs = tuple(state.get("report_artifact_refs", {}).get(dependency.task.task_id, ()))
                if not refs:
                    raise ReportArtifactNotFoundError("missing_report_artifact")
                for reference in refs:
                    artifact = await report_artifacts.get(
                        UUID(reference),
                        principal_user_id=principal_user_id,
                        session_id=session_id,
                        interaction_id=origin_interaction_id,
                        task_id=dependency.task.task_id,
                    )
                    validate_report_source(
                        artifact.envelope,
                        principal_user_id=principal_user_id,
                        lot_id=artifact.envelope.report.requested_lot.lot_id,
                        authorized_property_ids=authorized_property_ids,
                    )
                    report_artifact_scopes[reference] = {
                        "interaction_id": str(origin_interaction_id),
                        "task_id": dependency.task.task_id,
                    }
                report_refs_by_dependency[dependency.task.task_id] = refs
        except ReportArtifactExpiredError, ReportSourceExpiredError:
            if state.get("report_source_renewal_count", 0) < 1:
                source_index = next(
                    index
                    for index, candidate_task in enumerate(state["ordered_tasks"])
                    if candidate_task.task_id == farm_dependencies[0].task.task_id
                )
                prior_outcomes = tuple(
                    outcome
                    for outcome in state.get("task_outcomes", ())
                    if outcome.task.task_id
                    in {candidate.task_id for candidate in state["ordered_tasks"][:source_index]}
                )
                prior_task_ids = {outcome.task.task_id for outcome in prior_outcomes}
                prior_sources = unique_sources(
                    [source for outcome in prior_outcomes for source in outcome.result.sources]
                )
                return {
                    "current_task_index": source_index,
                    "task_outcomes": prior_outcomes,
                    "sources": prior_sources,
                    "report_artifact_refs": {
                        key: value
                        for key, value in state.get("report_artifact_refs", {}).items()
                        if key in prior_task_ids
                    },
                    "report_source_renewal_count": (
                        state.get("report_source_renewal_count", 0) + 1
                    ),
                    "errors": [
                        *state.get("errors", []),
                        "expired_report_source_renewed_by_farm",
                    ],
                }
            blocked = True
            errors = [*state.get("errors", []), "report_source_expired_after_renewal"]
        except ReportArtifactNotFoundError, ReportSourceValidationError, ValueError:
            blocked = True
            errors.append("invalid_or_missing_report_artifact")

    called = list(state.get("called_agents", []))

    if blocked:
        result = SpecialistResult(
            agent=task.agent,
            status=SpecialistStatus.ABSTAIN,
            answer="Uma tarefa anterior não forneceu os dados necessários.",
        )
        errors.append("blocked_dependency")
    else:
        dependency_results = tuple(
            DependencyResult(
                task_id=outcome.task.task_id,
                answer=outcome.result.answer,
                source_references=tuple(source.reference for source in outcome.result.sources),
                report_artifact_refs=report_refs_by_dependency.get(outcome.task.task_id, ()),
            )
            for outcome in dependencies
        )
        agent_input = SpecialistInput(
            task_id=task.task_id,
            goal=task.goal,
            brief_sanitized=task.brief_sanitized,
            context_refs=task.context_refs,
            depends_on=task.depends_on,
            required_evidence=task.required_evidence,
            dependency_results=dependency_results,
            session_context=_history_context(state),
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
            response = await factory.create(agent_id).run(agent_input, context=context)
            result = SpecialistResult.model_validate(response)
            if result.agent.value != agent_id.value:
                raise ValueError("specialist_agent_mismatch")
        except ExecutionEventWriteError:
            raise
        except Exception:
            result = SpecialistResult(
                agent=task.agent,
                status=SpecialistStatus.ERROR,
                error="specialist_unavailable",
            )
            errors.append(f"{agent_id.value}_failed")

    if agent_id == AgentId.SECRETARY and email_preparations is not None:
        configurable = (config or {}).get("configurable") or {}
        try:
            trusted_recipient_question = await email_preparations.get_pending_recipient_resolution(
                principal_user_id=UUID(str(configurable["argos_user_id"])),
                session_id=UUID(state["session_id"]),
                interaction_id=UUID(state["interaction_id"]),
            )
        except Exception:
            result = SpecialistResult(
                agent=task.agent,
                status=SpecialistStatus.ERROR,
                error="recipient_resolution_storage_unavailable",
            )
            errors.append("recipient_resolution_storage_unavailable")
        if trusted_recipient_question is not None:
            result = SpecialistResult(
                agent=task.agent,
                status=SpecialistStatus.NEEDS_CLARIFICATION,
                answer="É necessário resolver um destinatário antes de preparar o relatório.",
                missing_fields=("recipient",),
            )

    outcome = TaskOutcome(task=task, result=result)
    if result.status in (SpecialistStatus.ERROR, SpecialistStatus.UNAVAILABLE):
        errors.append(f"{agent_id.value}_{result.status.value}")
    sources = unique_sources([*state.get("sources", []), *result.sources])
    update: dict[str, Any] = {
        "called_agents": called,
        "errors": errors,
        "current_task_index": index + 1,
        "task_outcomes": (*state.get("task_outcomes", ()), outcome),
        "sources": sources,
    }
    if agent_id == AgentId.FARM and report_artifacts is not None:
        configurable = (config or {}).get("configurable") or {}
        try:
            principal_user_id = UUID(str(configurable["argos_user_id"]))
            session_id = UUID(state["session_id"])
            interaction_id = UUID(state["interaction_id"])
            artifacts = await report_artifacts.list_for_task(
                principal_user_id=principal_user_id,
                session_id=session_id,
                interaction_id=interaction_id,
                task_id=task.task_id,
            )
            for artifact in artifacts:
                validate_report_source(
                    artifact.envelope,
                    principal_user_id=principal_user_id,
                    lot_id=artifact.envelope.report.requested_lot.lot_id,
                    authorized_property_ids=tuple(
                        UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
                    ),
                    now=artifact.created_at,
                )
            refs = tuple(str(artifact.reference) for artifact in artifacts)
            refs_by_task = dict(state.get("report_artifact_refs", {}))
            if refs:
                refs_by_task[task.task_id] = refs
                update["report_artifact_origin_interaction_id"] = str(interaction_id)
            else:
                refs_by_task.pop(task.task_id, None)
            update["report_artifact_refs"] = refs_by_task
        except Exception:
            update["report_artifact_refs"] = {
                key: value
                for key, value in state.get("report_artifact_refs", {}).items()
                if key != task.task_id
            }
            update["errors"] = [*update["errors"], "invalid_report_artifact_created"]
    if (
        result.status in (SpecialistStatus.NEEDS_CLARIFICATION, SpecialistStatus.NEEDS_INPUT)
        or result.missing_fields
    ):
        fields = list(dict.fromkeys(result.missing_fields))
        goal = ", ".join(fields) if fields else task.goal
        update["missing_fields"] = fields
        update["clarification_request"] = QuestionFormattingRequest(
            reason_code="SPECIALIST_NEEDS_INPUT",
            input_type="free_text",
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


async def no_orquestrador(
    state: AgentState,
    *,
    factory: AgentFactory,
    config: RunnableConfig | None = None,
) -> dict:
    called = [*state.get("called_agents", []), AgentId.ORCHESTRATOR.value]
    previous_review = state.get("judge_result")
    feedback = (
        previous_review.reason
        if previous_review and previous_review.decision == JudgeDecision.REVISE
        else None
    )
    try:
        response = await factory.create(AgentId.ORCHESTRATOR).run(
            OrchestratorInput(
                request=state["sanitized_message"],
                outcomes=state["task_outcomes"],
                revision_feedback=feedback,
                session_context=_preference_context(state),
            ),
            context=build_agent_context(state, config=config),
        )
        draft = OrchestratorResult.model_validate(response)
        trusted_sources = state.get("sources", [])
        if any(source not in trusted_sources for source in draft.sources):
            raise ValueError("unknown_source")
    except ExecutionEventWriteError:
        raise
    except Exception:
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), "orchestrator_failed_or_unknown_source"],
            "final_answer": SAFE_ANSWER,
        }
    return {"called_agents": called, "draft_result": draft}


async def no_juiz(
    state: AgentState,
    *,
    factory: AgentFactory,
    config: RunnableConfig | None = None,
) -> dict:
    called = [*state.get("called_agents", []), AgentId.JUDGE.value]
    draft = state.get("draft_result")
    if draft is None:
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), "missing_draft"],
            "final_answer": SAFE_ANSWER,
        }
    try:
        response = await factory.create(AgentId.JUDGE).run(
            JudgeInput(
                request=state["sanitized_message"],
                draft_answer=draft.draft_answer,
                sources=draft.sources,
                outcomes=state["task_outcomes"],
            ),
            context=build_agent_context(state, config=config),
        )
        review = JudgeResult.model_validate(response)
    except ExecutionEventWriteError:
        raise
    except Exception:
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), "judge_failed"],
            "final_answer": SAFE_ANSWER,
        }
    retry_count = state.get("retry_count", 0)
    if review.decision == JudgeDecision.REVISE:
        retry_count += 1
    return {"called_agents": called, "judge_result": review, "retry_count": retry_count}


async def no_formatador(
    state: AgentState,
    *,
    factory: AgentFactory,
    config: RunnableConfig | None = None,
) -> dict:
    called = [*state.get("called_agents", []), AgentId.QUESTION_FORMATTER.value]
    request = state.get("clarification_request")
    if request is None:
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), "missing_question_request"],
            "final_answer": SAFE_ANSWER,
        }
    try:
        response = await factory.create(AgentId.QUESTION_FORMATTER).run(
            QuestionFormatterInput(request=request),
            context=build_agent_context(state, config=config),
        )
        question = response.prompt.strip()
        if not question:
            raise ValueError("empty_question")
    except ExecutionEventWriteError:
        raise
    except Exception:
        question = request.question_goal
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), "question_formatter_failed"],
            "clarification_question": question,
            "question_id": state.get("question_id") or str(uuid4()),
        }
    return {
        "called_agents": called,
        "clarification_question": question,
        "question_id": state.get("question_id") or str(uuid4()),
    }


def no_guardrail_pergunta(state: AgentState) -> dict:
    question = minimize_pii(state.get("clarification_question") or SAFE_ANSWER)
    for identifier in build_agent_context(state).values():
        if identifier:
            question = question.replace(identifier, "[identificador protegido]")
    return {"clarification_question": question, "messages": [AIMessage(content=question)]}


def no_guardrail_saida(state: AgentState) -> dict:
    review = state.get("judge_result")
    draft = state.get("draft_result")
    if (
        review is None
        or review.decision != JudgeDecision.APPROVED
        or review.unsupported_claims
        or draft is None
    ):
        answer = SAFE_ANSWER
    else:
        answer = minimize_pii(draft.draft_answer)
        for identifier in build_agent_context(state).values():
            if identifier:
                answer = answer.replace(identifier, "[identificador protegido]")
    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def no_saida_segura(state: AgentState) -> dict:
    answer = state.get("final_answer") or SAFE_ANSWER
    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def decidir_pos_entrada(state: AgentState) -> str:
    if state.get("final_answer"):
        return "safe"
    return state.get("resume_target") or "router"


def decidir_pos_roteador(state: AgentState) -> str:
    if state.get("final_answer"):
        return "safe"
    if state.get("clarification_request"):
        return "question_formatter"
    return state["ordered_tasks"][0].agent.value


def decidir_proximo_especialista(state: AgentState) -> str:
    if state.get("final_answer"):
        return "safe"
    if state.get("clarification_request"):
        return "question_formatter"
    index = state["current_task_index"]
    if index < len(state["ordered_tasks"]):
        return state["ordered_tasks"][index].agent.value
    return "orchestrator"


def decidir_pos_orquestrador(state: AgentState) -> str:
    return "safe" if state.get("final_answer") else "judge"


def decidir_pos_juiz(state: AgentState) -> str:
    if state.get("final_answer"):
        return "safe"
    review = state.get("judge_result")
    if review and review.decision == JudgeDecision.APPROVED and not review.unsupported_claims:
        return "guardrail_output"
    if review and review.decision == JudgeDecision.REVISE and state["retry_count"] <= MAX_REVISIONS:
        return "orchestrator"
    return "safe"


def decidir_pos_formatador(state: AgentState) -> str:
    return "safe" if state.get("final_answer") else "question_guardrail"


def build_graph(
    factory: AgentFactory,
    *,
    checkpointer: Any = None,
    report_artifacts: ReportArtifactRepository | None = None,
    email_preparations: EmailPreparationRepository | None = None,
):
    graph = StateGraph(AgentState)
    graph.add_node("guardrail_input", no_guardrail_entrada)
    graph.add_node("router", partial(no_roteador, factory=factory))
    for agent_id in (
        AgentId.FARM,
        AgentId.DOCTOR,
        AgentId.ANALYST,
        AgentId.SECRETARY,
        AgentId.INFORMATION,
    ):
        graph.add_node(
            agent_id.value,
            partial(
                no_especialista,
                factory=factory,
                agent_id=agent_id,
                report_artifacts=report_artifacts,
                email_preparations=email_preparations,
            ),
        )
    graph.add_node("orchestrator", partial(no_orquestrador, factory=factory))
    graph.add_node("judge", partial(no_juiz, factory=factory))
    graph.add_node("question_formatter", partial(no_formatador, factory=factory))
    graph.add_node("question_guardrail", no_guardrail_pergunta)
    graph.add_node("guardrail_output", no_guardrail_saida)
    graph.add_node("safe", no_saida_segura)

    specialist_routes = {
        "farm": "farm",
        "doctor": "doctor",
        "analyst": "analyst",
        "secretary": "secretary",
        "information": "information",
        "question_formatter": "question_formatter",
        "orchestrator": "orchestrator",
        "safe": "safe",
    }
    graph.add_edge(START, "guardrail_input")
    graph.add_conditional_edges(
        "guardrail_input",
        decidir_pos_entrada,
        {
            "router": "router",
            "farm": "farm",
            "doctor": "doctor",
            "analyst": "analyst",
            "secretary": "secretary",
            "information": "information",
            "safe": "safe",
        },
    )
    graph.add_conditional_edges("router", decidir_pos_roteador, specialist_routes)
    for specialist in ("farm", "doctor", "analyst", "secretary", "information"):
        graph.add_conditional_edges(specialist, decidir_proximo_especialista, specialist_routes)
    graph.add_conditional_edges(
        "orchestrator", decidir_pos_orquestrador, {"judge": "judge", "safe": "safe"}
    )
    graph.add_conditional_edges(
        "judge",
        decidir_pos_juiz,
        {
            "orchestrator": "orchestrator",
            "guardrail_output": "guardrail_output",
            "safe": "safe",
        },
    )
    graph.add_conditional_edges(
        "question_formatter",
        decidir_pos_formatador,
        {"question_guardrail": "question_guardrail", "safe": "safe"},
    )
    graph.add_edge("question_guardrail", END)
    graph.add_edge("guardrail_output", END)
    graph.add_edge("safe", END)
    return graph.compile(checkpointer=checkpointer)


async def executar_fluxo(
    graph: Any,
    message: str,
    *,
    user_id: str,
    farm_id: str,
    session_id: str,
    interaction_id: str | None = None,
    session_context: SessionContext | None = None,
    recipient_selectors: dict[str, dict[str, str]] | None = None,
    send_intent_requested: bool = False,
    config: RunnableConfig | None = None,
) -> dict:
    state = initial_state(
        message,
        user_id=user_id,
        farm_id=farm_id,
        session_id=session_id,
        interaction_id=interaction_id,
        session_context=session_context,
        recipient_selectors=recipient_selectors,
        send_intent_requested=send_intent_requested,
    )
    return await graph.ainvoke(
        state,
        config=build_session_config(
            session_id,
            config,
            checkpoint_namespace=f"interaction-{interaction_id}" if interaction_id else None,
        ),
    )


async def executar_resposta(
    graph: Any,
    answer: str,
    *,
    question_id: str,
    session_id: str,
    interaction_id: str | None = None,
    session_context: SessionContext | None = None,
    recipient_selectors: dict[str, dict[str, str]] | None = None,
    send_intent_requested: bool = False,
    send_intent_source_interaction_id: str | None = None,
    checkpoint_namespace: str | None = None,
    config: RunnableConfig | None = None,
) -> dict:
    state = answer_state(
        answer,
        question_id=question_id,
        interaction_id=interaction_id,
        session_context=session_context,
        recipient_selectors=recipient_selectors,
        send_intent_requested=send_intent_requested,
        send_intent_source_interaction_id=send_intent_source_interaction_id,
    )
    return await graph.ainvoke(
        state,
        config=build_session_config(
            session_id,
            config,
            checkpoint_namespace=(
                f"interaction-{checkpoint_namespace}" if checkpoint_namespace else None
            ),
        ),
    )


def build_session_config(
    session_id: str,
    config: RunnableConfig | None,
    *,
    checkpoint_namespace: str | None = None,
) -> RunnableConfig:
    merged_config: RunnableConfig = dict(config or {})
    configurable = dict(merged_config.get("configurable") or {})
    configurable["thread_id"] = session_id
    if checkpoint_namespace is not None:
        configurable["checkpoint_ns"] = checkpoint_namespace
    merged_config["configurable"] = configurable
    return merged_config
