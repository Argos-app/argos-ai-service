"""Modelo determinístico local para exercitar o grafo sem chaves externas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class SimulatedChatModel(BaseChatModel):
    """Produz chamadas estruturadas fixas e aciona tools reais do serviço."""

    @property
    def _llm_type(self) -> str:
        return "argos_simulated"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any:
        return self.bind(tools=tools, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        tools = kwargs.get("tools") or ()
        names = [_tool_name(tool) for tool in tools]
        structured = next((name for name in names if name in _STRUCTURED_NAMES), None)
        business_tools = [name for name in names if name and name not in _STRUCTURED_NAMES]
        tool_result_seen = any(isinstance(message, ToolMessage) for message in messages)
        if structured == "SpecialistResult" and business_tools and not tool_result_seen:
            tool_name = business_tools[0]
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": tool_name, "args": _tool_arguments(tool_name), "id": str(uuid4())}
                ],
            )
        elif structured:
            call_name, args = self._structured_call(structured, messages)
            message = AIMessage(
                content="",
                tool_calls=[{"name": call_name, "args": args, "id": str(uuid4())}],
            )
        elif tools:
            tool_name = next((name for name in names if name), "")
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": tool_name, "args": _tool_arguments(tool_name), "id": str(uuid4())}
                ],
            )
        else:
            message = AIMessage(content="Execução concluída em modo simulado.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _structured_call(name: str, messages: list[BaseMessage]) -> tuple[str, dict[str, Any]]:
        if name == "RoutePlan":
            request = _last_human_text(messages).casefold()
            agent = "farm"
            if any(term in request for term in ("consumo", "previsão", "previsao")):
                agent = "analyst"
            elif any(term in request for term in ("medicamento", "remédio", "remedio", "bula")):
                agent = "doctor"
            elif any(
                term in request for term in ("lei", "contrato", "documento", "juríd", "jurid")
            ):
                agent = "information"
            return name, {
                "intent": "local_simulation",
                "tasks": [
                    {
                        "task_id": "simulated_task",
                        "agent": agent,
                        "goal": "Consultar a fonte disponível para a solicitação.",
                        "brief_sanitized": "Solicitação enviada no modo simulado.",
                        "required_evidence": ["resultado da ferramenta"],
                    }
                ],
            }
        if name == "SpecialistResult":
            agent = "farm"
            tool_result_seen = False
            for message in reversed(messages):
                if isinstance(message, ToolMessage):
                    tool_result_seen = True
                    agent = _agent_for_tool(getattr(message, "name", ""))
                    break
            return name, {
                "agent": agent,
                "status": "success" if tool_result_seen else "unavailable",
                "answer": (
                    "Consulta local concluída usando o resultado da ferramenta."
                    if tool_result_seen
                    else "Nenhuma consulta foi executada."
                ),
                "sources": [],
            }
        if name == "OrchestratorResult":
            return name, {
                "draft_answer": "Consulta local simulada concluída com os resultados disponíveis.",
                "sources": [],
            }
        if name == "JudgeResult":
            return name, {
                "decision": "approved",
                "reason": "Resposta baseada no resultado observado.",
            }
        if name == "QuestionFormatterResult":
            return name, {"prompt": "Qual informação adicional você pode fornecer?"}
        if name == "UserMemoryDraft":
            unchanged = {"action": "unchanged", "value": None, "source_excerpt": None}
            return name, {"tone": unchanged, "general_goal": unchanged}
        return name, {}


_STRUCTURED_NAMES = {
    "RoutePlan",
    "SpecialistResult",
    "OrchestratorResult",
    "JudgeResult",
    "QuestionFormatterResult",
    "UserMemoryDraft",
}


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "")
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if message.type == "human":
            return str(message.content)
    return ""


def _agent_for_tool(name: str) -> str:
    if name.startswith("list_medicine"):
        return "doctor"
    if name == "estimate_medicine_consumption":
        return "analyst"
    if name in {"prepare_report", "send_report", "get_report_status"}:
        return "secretary"
    if "document" in name or "legal" in name:
        return "information"
    return "farm"


def _tool_arguments(name: str) -> dict[str, Any]:
    if name == "consultar_documental":
        return {"question": "Consulte os documentos sintéticos disponíveis."}
    if name in {"list_properties", "list_medicines", "list_herds", "list_lots"}:
        return {"payload": {}}
    if name == "estimate_medicine_consumption":
        return {
            "payload": {
                "as_of": (datetime.now(UTC).date() - timedelta(days=1)).isoformat(),
                "horizon_days": 7,
            }
        }
    return {"payload": {}}
