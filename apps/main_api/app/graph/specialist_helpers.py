"""Helpers compartilhados pela composição dos agentes e especialistas."""

from collections.abc import Mapping
from typing import Any

from app.agent_schemas.inputs import SessionContext
from app.agent_schemas.results import SourceReference
from app.application.execution_limits import ExecutionLimits, current_execution_limits
from app.graph.state import AgentState
from langchain_core.runnables import RunnableConfig


def agent_failure_reason(agent: str, error: BaseException) -> str:
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
            "StructuredOutputValidationError",
            "MultipleStructuredOutputsError",
        } or isinstance(current, (TypeError, ValueError)):
            return f"{agent}_invalid_output"
        current = current.__cause__ or current.__context__
    return f"{agent}_execution_failed"


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
    for key in (
        "argos_user_id",
        "argos_firebase_uid",
        "argos_authorized_property_ids",
        "argos_request_id",
    ):
        if configurable.get(key):
            context[key] = configurable[key]
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


def history_context(state: AgentState) -> SessionContext:
    context = state.get("session_context") or SessionContext()
    return SessionContext(
        session_summary=context.session_summary,
        recent_messages=context.recent_messages,
    )


def unique_sources(sources: list[SourceReference]) -> list[SourceReference]:
    unique: list[SourceReference] = []
    for source in sources:
        if source not in unique:
            unique.append(source)
    return unique


def email_dispatch_recovery_context(
    state: AgentState, configurable: Mapping[str, Any]
) -> dict[str, Any]:
    """Monta o escopo confiável para recuperar o estado de envio da interação."""
    context = dict(configurable)
    context["argos_user_id"] = state["user_id"]
    context["session_id"] = state["session_id"]
    source_interaction_id = state.get("send_intent_source_interaction_id")
    if source_interaction_id:
        context["argos_send_intent_source_interaction_id"] = source_interaction_id
    return context
