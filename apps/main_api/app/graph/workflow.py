"""Composição do grafo conversacional e API pública de execução."""

from functools import partial
from typing import Any

from app.agent_schemas.inputs import SessionContext
from app.application.email_dispatch_workflow import EmailDispatchWorkflow
from app.graph.state import AgentState
from app.graph.workflow_nodes import (
    SYNTHESIS_AGENT_TIMEOUT_SECONDS,
    answer_state,
    initial_state,
    route_after_input_guardrail,
    route_after_judge,
    route_after_orchestrator,
    route_after_question_formatter,
    route_after_router,
    route_after_specialist,
    run_input_guardrail_node,
    run_judge_node,
    run_orchestrator_node,
    run_output_guardrail_node,
    run_question_formatter_node,
    run_question_guardrail_node,
    run_router_node,
    run_safe_response_node,
    run_specialist_node,
)
from app.ports.agent import AgentId
from app.ports.agent_factory import AgentFactory
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.report_artifact_repository import ReportArtifactRepository
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

__all__ = [
    "build_graph",
    "build_session_config",
    "execute_workflow",
    "resume_workflow_answer",
]


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
    graph.add_node("guardrail_input", run_input_guardrail_node)
    graph.add_node("router", partial(run_router_node, factory=factory))
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
                run_specialist_node,
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
            run_orchestrator_node,
            factory=factory,
            timeout_seconds=synthesis_agent_timeout_seconds,
        ),
    )
    graph.add_node(
        "judge",
        partial(
            run_judge_node,
            factory=factory,
            timeout_seconds=synthesis_agent_timeout_seconds,
        ),
    )
    graph.add_node("question_formatter", partial(run_question_formatter_node, factory=factory))
    graph.add_node("question_guardrail", run_question_guardrail_node)
    graph.add_node("guardrail_output", run_output_guardrail_node)
    graph.add_node("safe", run_safe_response_node)

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
        route_after_input_guardrail,
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
    graph.add_conditional_edges("router", route_after_router, specialist_routes)
    for specialist in ("farm", "doctor", "analyst", "secretary", "information"):
        graph.add_conditional_edges(specialist, route_after_specialist, specialist_routes)
    graph.add_conditional_edges(
        "orchestrator", route_after_orchestrator, {"judge": "judge", "safe": "safe"}
    )
    graph.add_conditional_edges(
        "judge",
        route_after_judge,
        {
            "orchestrator": "orchestrator",
            "guardrail_output": "guardrail_output",
            "safe": "safe",
        },
    )
    graph.add_conditional_edges(
        "question_formatter",
        route_after_question_formatter,
        {"question_guardrail": "question_guardrail", "safe": "safe"},
    )
    graph.add_edge("question_guardrail", END)
    graph.add_edge("guardrail_output", END)
    graph.add_edge("safe", END)
    return graph.compile(checkpointer=checkpointer)


async def execute_workflow(
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


async def resume_workflow_answer(
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
    *,
    checkpoint_namespace: str | None = None,
) -> RunnableConfig:
    """LangGraph usa namespace vazio no grafo raiz. Um namespace próprio impediria
    o MongoSaver de localizar as gravações por aget_state(thread_id)."""
    merged_config: RunnableConfig = dict(config or {})
    configurable = dict(merged_config.get("configurable") or {})
    configurable["thread_id"] = session_id

    configurable.pop("checkpoint_ns", None)
    merged_config["configurable"] = configurable
    return merged_config
