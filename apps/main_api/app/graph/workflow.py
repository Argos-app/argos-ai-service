import asyncio
import logging
import re
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from app.agent_schemas.inputs import (
    DependencyEvidence,
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
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.application.execution_limits import (
    ExecutionLimits,
    current_execution_limits,
)
from app.application.report_artifact_service import (
    ReportSourceExpiredError,
    ReportSourceValidationError,
    validate_report_source,
)
from app.application.verified_evidence import verified_evidence_fallback
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
SYNTHESIS_AGENT_TIMEOUT_SECONDS = 60
SYNTHESIS_AGENT_TIMEOUT_GRACE_SECONDS = 15.0
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."
NO_VERIFIED_RESULT_ANSWER = (
    "Não há resultado verificável para responder: um agente necessário falhou ou se absteve. "
    "O conteúdo solicitado permanece desconhecido; tente novamente."
)
ROUTER_UNAVAILABLE_ANSWER = (
    "Não consegui iniciar o processamento desta solicitação agora. "
    "Nenhuma consulta de dados foi concluída; tente novamente."
)
SYNTHESIS_UNAVAILABLE_ANSWER = (
    "Os agentes não conseguiram consolidar os resultados em uma resposta verificável. "
    "Tente novamente."
)
JUDGE_UNAVAILABLE_ANSWER = (
    "A validação final da resposta não foi concluída; por segurança, não vou apresentar "
    "um resultado sem revisão. Tente novamente."
)
_LOGGER = logging.getLogger(__name__)


def synthesis_timeout_with_runtime_grace(
    *, model_timeout_seconds: float, execution_timeout_seconds: float
) -> float:
    """Deixa a gravação e validação da resposta concluírem após a chamada do modelo."""
    return min(
        float(model_timeout_seconds) + SYNTHESIS_AGENT_TIMEOUT_GRACE_SECONDS,
        float(execution_timeout_seconds),
    )


def _judge_outcome_summaries(outcomes: tuple[TaskOutcome, ...]) -> tuple[TaskOutcome, ...]:
    """Avoids duplicating full source excerpts in the Judge's complete evidence list."""
    return tuple(
        outcome.model_copy(
            update={
                "result": outcome.result.model_copy(
                    update={
                        "sources": tuple(
                            source.model_copy(update={"excerpt": None})
                            for source in outcome.result.sources
                        )
                    }
                )
            }
        )
        for outcome in outcomes
    )

SECRET_REQUEST_PATTERN = re.compile(
    r"\b(?:revele|mostre|exiba)\b.{0,60}"
    r"\b(?:chave de api|token de acesso|senha|prompt de sistema)\b",
    re.IGNORECASE,
)


def _agent_failure_reason(agent: str, error: BaseException) -> str:
    """Converte falhas técnicas em códigos públicos seguros e específicos."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status_code = getattr(current, "status_code", None)
        if status_code == 429:
            return f"{agent}_provider_rate_limited"
        if status_code in {408, 504} or isinstance(current, TimeoutError):
            return f"{agent}_provider_timeout"
        if isinstance(status_code, int) and 500 <= status_code <= 599:
            return f"{agent}_provider_unavailable"
        if status_code in {401, 403}:
            return f"{agent}_provider_rejected"
        if type(current).__name__ in {
            "AgentResponseError",
            "OutputParserException",
            "ValidationError",
        } or isinstance(current, (TypeError, ValueError)):
            return f"{agent}_invalid_output"
        current = current.__cause__ or current.__context__
    return f"{agent}_execution_failed"


def _all_agent_results_failed_without_evidence(state: AgentState) -> bool:
    outcomes = state.get("task_outcomes", ())
    if not outcomes or any(source.excerpt for source in state.get("sources", ())):
        return False
    unavailable_statuses = {
        SpecialistStatus.ABSTAIN,
        SpecialistStatus.ERROR,
        SpecialistStatus.UNAVAILABLE,
    }
    return all(outcome.result.status in unavailable_statuses for outcome in outcomes)
_REPORT_WORD_PATTERN = re.compile(r"\brelat[óo]rio\b", re.IGNORECASE)
_REPORT_ACTION_PATTERN = re.compile(
    r"\b(?:prepar\w*|ger\w*|cri\w*|mont\w*|envie|enviar|mande|mandar|"
    r"encaminhe|encaminhar)\b",
    re.IGNORECASE,
)
_EXISTING_REPORT_PATTERN = re.compile(
    r"\b(?:já\s+)?(?:(?:está|foi|estava)\s+)?(?:preparad\w*|gerad\w*|criad\w*)\b",
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
    limits = current_execution_limits()
    if isinstance(limits, ExecutionLimits):
        context["argos_execution_limits"] = limits
    if report_artifact_scopes:
        context["argos_report_artifact_scopes"] = report_artifact_scopes
    if recipient_selectors is not None:
        context["recipient_selectors"] = recipient_selectors
        context["argos_send_intent_requested"] = bool(state.get("send_intent_requested"))
        context["argos_send_intent_source_interaction_id"] = state.get(
            "send_intent_source_interaction_id"
        )
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
        "email_action_summary": None,
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


def _require_report_source_dependency(plan: RoutePlan, message: str) -> RoutePlan:
    """Impõe Fazenda → Secretário para gerar/enviar um relatório-fonte novo."""
    if (
        not _REPORT_WORD_PATTERN.search(message)
        or not _REPORT_ACTION_PATTERN.search(message)
        or _EXISTING_REPORT_PATTERN.search(message)
        or plan.missing_slots
    ):
        return plan
    secretary_tasks = [task for task in plan.tasks if task.agent == AgentId.SECRETARY]
    if not secretary_tasks:
        return plan

    tasks = list(plan.tasks)
    farm_tasks = [task for task in tasks if task.agent == AgentId.FARM]
    if not farm_tasks and len(tasks) < MAX_TASKS:
        task_id = "report-source"
        suffix = 2
        while any(task.task_id == task_id for task in tasks):
            task_id = f"report-source-{suffix}"
            suffix += 1
        farm_tasks = [
            RouteTask(
                task_id=task_id,
                agent=AgentId.FARM,
                goal="Resolver a fonte assinada do relatório solicitado",
                brief_sanitized=(
                    "Resolver o lote e obter a fonte assinada do relatório com "
                    "resolve_report_lot_report_source; não substituir por list_lots."
                ),
                required_evidence=("signed_report_source",),
            )
        ]
        tasks.insert(0, farm_tasks[0])

    farm_ids = {task.task_id for task in farm_tasks}
    if not farm_ids:
        return plan
    required_instruction = (
        "Este relatório será preparado pelo Secretário. Como requisito obrigatório, "
        "chame resolve_report_lot_report_source com o lote identificado; list_lots "
        "sozinho não fornece o artefato assinado."
    )
    normalized_tasks: list[RouteTask] = []
    for task in tasks:
        if task.agent == AgentId.FARM and task.task_id in farm_ids:
            task = task.model_copy(
                update={
                    "brief_sanitized": f"{task.brief_sanitized}\n{required_instruction}",
                    "required_evidence": tuple(
                        dict.fromkeys((*task.required_evidence, "signed_report_source"))
                    ),
                }
            )
        elif task.agent == AgentId.SECRETARY:
            task = task.model_copy(
                update={
                    "depends_on": tuple(dict.fromkeys((*task.depends_on, *farm_ids)))
                }
            )
        normalized_tasks.append(task)
    return plan.model_copy(update={"tasks": tuple(normalized_tasks)})


def _canonicalize_sources(
    candidate_sources: tuple[SourceReference, ...],
    trusted_sources: list[SourceReference],
) -> tuple[SourceReference, ...]:
    """Aceita somente referências emitidas pelo fluxo e recupera seus metadados confiáveis."""
    canonical_by_reference = {source.reference: source for source in trusted_sources}
    requested_references = tuple(dict.fromkeys(source.reference for source in candidate_sources))
    if any(reference not in canonical_by_reference for reference in requested_references):
        raise ValueError("unknown_source")
    return tuple(canonical_by_reference[reference] for reference in requested_references)


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
    protected_text = protected.sanitized_text
    protected_recipients = protected.recipient_selectors
    if state.get("input_kind") == "answer" and state.get("sanitized_message"):
        boundary = "\n<ARGOS_ANSWER_BOUNDARY>\n"
        contextual = protect_user_input(f"{state['sanitized_message']}{boundary}{raw_text}")
        protected_text = contextual.sanitized_text.split(boundary, maxsplit=1)[1]
        protected_recipients = contextual.recipient_selectors
    recipient_selectors, sanitized = merge_recipient_selectors(
        dict(state.get("recipient_selectors") or {}),
        protected_recipients,
        protected_text,
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
        plan = _require_report_source_dependency(plan, state["sanitized_message"])
        ordered = order_tasks(plan)
    except ExecutionEventWriteError:
        raise
    except Exception as exc:
        failure_reason = _agent_failure_reason(AgentId.ROUTER.value, exc)
        _LOGGER.warning(
            "agent execution failed: %s",
            type(exc).__name__,
            extra={"agent": AgentId.ROUTER.value, "failure_reason": failure_reason},
        )
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), failure_reason],
            "final_answer": ROUTER_UNAVAILABLE_ANSWER,
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
        except (ReportArtifactExpiredError, ReportSourceExpiredError):
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
                }
            blocked = True
            errors = [*state.get("errors", []), "report_source_expired_after_renewal"]
        except (ReportArtifactNotFoundError, ReportSourceValidationError, ValueError):
            blocked = True
            errors.append("invalid_or_missing_report_artifact")

    called = list(state.get("called_agents", []))
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
                if not own_evidence and not inherited_sources and result.status in {
                    SpecialistStatus.SUCCESS,
                    SpecialistStatus.PARTIAL,
                }:
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
                            "sources": tuple(
                                unique_sources([*source_evidence, *inherited_sources])
                            )
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
            errors.append(failure_reason)

    if agent_id == AgentId.SECRETARY and email_preparations is not None:
        configurable = (config or {}).get("configurable") or {}
        try:
            trusted_recipient_question = await email_preparations.get_pending_recipient_resolution(
                principal_user_id=UUID(str(configurable["argos_user_id"])),
                session_id=UUID(state["session_id"]),
                interaction_id=UUID(state["interaction_id"]),
            )
        except Exception:
            failure_reason = "recipient_resolution_storage_unavailable"
            result = SpecialistResult(
                agent=task.agent,
                status=SpecialistStatus.ERROR,
                error=failure_reason,
            )
            errors.append(failure_reason)
        if trusted_recipient_question is not None:
            result = SpecialistResult(
                agent=task.agent,
                status=SpecialistStatus.NEEDS_CLARIFICATION,
                answer="É necessário resolver um destinatário antes de preparar o relatório.",
                missing_fields=("recipient",),
            )

    email_action_summary = None
    if (
        agent_id == AgentId.SECRETARY
        and email_dispatch is not None
        and state.get("send_intent_requested")
    ):
        configurable = (config or {}).get("configurable") or {}
        recovery_context = dict(configurable)
        recovery_context.setdefault("argos_user_id", state["user_id"])
        recovery_context.setdefault("session_id", state["session_id"])
        if state.get("send_intent_source_interaction_id"):
            recovery_context.setdefault(
                "argos_send_intent_source_interaction_id",
                state["send_intent_source_interaction_id"],
            )
        email_action_summary = await email_dispatch.recover_interaction_outcome(
            configurable=recovery_context
        )

    outcome = TaskOutcome(task=task, result=result)
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
    if result.status in (SpecialistStatus.NEEDS_CLARIFICATION, SpecialistStatus.NEEDS_INPUT):
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
    timeout_seconds: float = SYNTHESIS_AGENT_TIMEOUT_SECONDS,
    config: RunnableConfig | None = None,
) -> dict:
    if _all_agent_results_failed_without_evidence(state):
        return {
            "final_answer": _unverified_result_answer(state),
        }
    called = [*state.get("called_agents", []), AgentId.ORCHESTRATOR.value]
    previous_review = state.get("judge_result")
    feedback = (
        previous_review.reason
        if previous_review and previous_review.decision == JudgeDecision.REVISE
        else None
    )
    try:
        async with asyncio.timeout(timeout_seconds):
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
        draft = draft.model_copy(
            update={"sources": _canonicalize_sources(draft.sources, trusted_sources)}
        )
    except ExecutionEventWriteError:
        raise
    except Exception as exc:
        failure_reason = _agent_failure_reason(AgentId.ORCHESTRATOR.value, exc)
        _LOGGER.warning(
            "agent execution failed: %s",
            type(exc).__name__,
            extra={"agent": AgentId.ORCHESTRATOR.value, "failure_reason": failure_reason},
        )
        limits = current_execution_limits()
        evidence = limits.evidence_snapshot() if isinstance(limits, ExecutionLimits) else ()
        fallback_answer = verified_evidence_fallback(evidence, unavailable_stage="síntese")
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), failure_reason],
            "final_answer": (
                _unverified_result_answer(state)
                if _all_agent_results_failed_without_evidence(state)
                else fallback_answer or SYNTHESIS_UNAVAILABLE_ANSWER
            ),
        }
    return {"called_agents": called, "draft_result": draft}


async def no_juiz(
    state: AgentState,
    *,
    factory: AgentFactory,
    timeout_seconds: float = SYNTHESIS_AGENT_TIMEOUT_SECONDS,
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
        async with asyncio.timeout(timeout_seconds):
            response = await factory.create(AgentId.JUDGE).run(
                JudgeInput(
                    request=state["sanitized_message"],
                    draft_answer=draft.draft_answer,
                    sources=tuple(unique_sources([*draft.sources, *state.get("sources", ())])),
                    outcomes=_judge_outcome_summaries(state["task_outcomes"]),
                ),
                context=build_agent_context(state, config=config),
            )
        review = JudgeResult.model_validate(response)
    except ExecutionEventWriteError:
        raise
    except Exception as exc:
        failure_reason = _agent_failure_reason(AgentId.JUDGE.value, exc)
        _LOGGER.warning(
            "agent execution failed: %s",
            type(exc).__name__,
            extra={"agent": AgentId.JUDGE.value, "failure_reason": failure_reason},
        )
        limits = current_execution_limits()
        evidence = limits.evidence_snapshot() if isinstance(limits, ExecutionLimits) else ()
        fallback_answer = verified_evidence_fallback(evidence, unavailable_stage="validação")
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), failure_reason],
            "final_answer": (
                _unverified_result_answer(state)
                if _all_agent_results_failed_without_evidence(state)
                else fallback_answer or JUDGE_UNAVAILABLE_ANSWER
            ),
        }
    retry_count = state.get("retry_count", 0)
    errors = list(state.get("errors", []))
    final_answer = None
    if review.decision == JudgeDecision.REVISE:
        retry_count += 1
        if retry_count > MAX_REVISIONS:
            errors.append("judge_revision_exhausted")
    elif review.decision == JudgeDecision.REJECTED:
        errors.append("judge_rejected")
    invalid_approval = (
        review.decision == JudgeDecision.APPROVED and bool(review.unsupported_claims)
    )
    if invalid_approval and "judge_invalid_output" not in errors:
        errors.append("judge_invalid_output")
    if invalid_approval or review.decision == JudgeDecision.REJECTED or (
        review.decision == JudgeDecision.REVISE and retry_count > MAX_REVISIONS
    ):
        limits = current_execution_limits()
        evidence = limits.evidence_snapshot() if isinstance(limits, ExecutionLimits) else ()
        final_answer = verified_evidence_fallback(evidence, unavailable_stage="validação")
    return {
        "called_agents": called,
        "judge_result": review,
        "retry_count": retry_count,
        "errors": errors,
        **({"final_answer": final_answer} if final_answer else {}),
    }


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
    except Exception as exc:
        failure_reason = _agent_failure_reason(AgentId.QUESTION_FORMATTER.value, exc)
        _LOGGER.warning(
            "agent execution failed: %s",
            type(exc).__name__,
            extra={"agent": AgentId.QUESTION_FORMATTER.value, "failure_reason": failure_reason},
        )
        question = request.question_goal
        return {
            "called_agents": called,
            "errors": [*state.get("errors", []), failure_reason],
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
        if isinstance(identifier, str) and identifier:
            question = question.replace(identifier, "[identificador protegido]")
    return {"clarification_question": question, "messages": [AIMessage(content=question)]}


def no_guardrail_saida(state: AgentState) -> dict:
    review = state.get("judge_result")
    draft = state.get("draft_result")
    if _all_agent_results_failed_without_evidence(state):
        answer = _unverified_result_answer(state)
    elif (
        review is None
        or review.decision != JudgeDecision.APPROVED
        or review.unsupported_claims
        or draft is None
    ):
        answer = SAFE_ANSWER
    else:
        answer = minimize_pii(draft.draft_answer)
        for identifier in build_agent_context(state).values():
            if isinstance(identifier, str) and identifier:
                answer = answer.replace(identifier, "[identificador protegido]")
    answer = _include_email_action_summary(answer, state.get("email_action_summary"))
    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def no_saida_segura(state: AgentState) -> dict:
    fallback = state.get("final_answer") or (
        _unverified_result_answer(state)
        if _all_agent_results_failed_without_evidence(state)
        else SAFE_ANSWER
    )
    answer = _include_email_action_summary(
        fallback,
        state.get("email_action_summary"),
    )
    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def _include_email_action_summary(answer: str, summary: str | None) -> str:
    if not summary:
        return answer
    return f"{answer}\n\nEstado do envio: {summary}"


def _unverified_result_answer(state: AgentState) -> str:
    """Explica indisponibilidade ou abstinência sem alegar que a busca não achou dados."""
    outcomes = state.get("task_outcomes", ())
    if any(outcome.result.status == SpecialistStatus.ABSTAIN for outcome in outcomes):
        return (
            "O agente necessário se absteve por não dispor de evidência validada. "
            "O conteúdo solicitado permanece desconhecido; tente novamente."
        )
    if any(outcome.result.status == SpecialistStatus.UNAVAILABLE for outcome in outcomes):
        return (
            "A integração necessária não retornou evidência verificável. "
            "O conteúdo solicitado permanece desconhecido; tente novamente."
        )
    return NO_VERIFIED_RESULT_ANSWER


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
    email_dispatch: EmailDispatchWorkflow | None = None,
    synthesis_agent_timeout_seconds: float = SYNTHESIS_AGENT_TIMEOUT_SECONDS,
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
                email_dispatch=email_dispatch,
            ),
        )
    graph.add_node(
        "orchestrator",
        partial(
            no_orquestrador,
            factory=factory,
            timeout_seconds=synthesis_agent_timeout_seconds,
        ),
    )
    graph.add_node(
        "judge",
        partial(
            no_juiz,
            factory=factory,
            timeout_seconds=synthesis_agent_timeout_seconds,
        ),
    )
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
    run_config = build_session_config(session_id, config)
    if send_intent_requested and not recipient_selectors:
        get_state = getattr(graph, "aget_state", None)
        if callable(get_state):
            snapshot = await get_state(run_config)
            saved = getattr(snapshot, "values", {}).get("recipient_selectors")
            if isinstance(saved, dict):
                recipient_selectors = saved
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
    return await graph.ainvoke(state, config=run_config)


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
        ),
    )


def build_session_config(
    session_id: str,
    config: RunnableConfig | None,
) -> RunnableConfig:
    merged_config: RunnableConfig = dict(config or {})
    configurable = dict(merged_config.get("configurable") or {})
    configurable["thread_id"] = session_id
    # LangGraph uses the empty namespace for the root graph. A custom namespace
    # here makes state writes invisible to aget_state(thread_id) in MongoSaver.
    configurable.pop("checkpoint_ns", None)
    merged_config["configurable"] = configurable
    return merged_config
