"""Valida a entrada e aplica risco antes de confirmar a retomada."""

from app.graph.state import AgentState
from app.graph.workflow_nodes import reject_input, run_input_guardrail_node
from app.ports.input_risk import InputRiskDecision, InputRiskEvaluator
from langchain_core.messages import AIMessage, HumanMessage


async def run_input_risk_node(state: AgentState, *, evaluator: InputRiskEvaluator) -> dict:
    candidate = run_input_guardrail_node(state)
    if candidate.get("final_answer"):
        return candidate
    message = next(
        (
            item
            for item in reversed(candidate.get("messages", []))
            if isinstance(item, HumanMessage)
        ),
        None,
    )
    try:
        decision = (
            await evaluator.evaluate(message.content)
            if message is not None and isinstance(message.content, str)
            else InputRiskDecision.UNAVAILABLE
        )
    except Exception:
        decision = InputRiskDecision.UNAVAILABLE
    if decision == InputRiskDecision.ALLOW:
        return candidate
    if decision == InputRiskDecision.BLOCK:
        result = reject_input(
            state,
            "Não posso atender a essa entrada por motivos de segurança.",
            "input_risk_blocked",
        )
    else:
        result = reject_input(
            state,
            "A verificação de segurança está temporariamente indisponível. "
            "Tente novamente mais tarde.",
            "input_risk_unavailable",
        )
    result["messages"] = [AIMessage(content=result["final_answer"])]
    return result
