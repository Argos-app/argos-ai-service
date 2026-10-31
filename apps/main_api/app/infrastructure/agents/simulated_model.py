"""Modelo determinístico local para exercitar o grafo sem chaves externas."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from app.privacy import minimize_sensitive_memory_input
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

    def get_num_tokens(self, text: str) -> int:
        """Estima tokens de forma local e conservadora, sem tokenizer externo.

        O simulador só precisa de uma medida determinística para respeitar os
        limites de contexto durante testes. Três bytes por token é deliberadamente
        conservador para texto UTF-8 e não depende de modelo ou provider."""
        if not text:
            return 0

        return (len(text.encode("utf-8")) + 2) // 3

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
        if structured == "SpecialistResult" and business_tools:
            specialist_input = _specialist_input(messages)
            tool_name = _next_tool(business_tools, specialist_input, messages)
            if tool_name is not None:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool_name,
                            "args": _tool_arguments(tool_name, specialist_input),
                            "id": str(uuid4()),
                        }
                    ],
                )
            else:
                call_name, args = self._structured_call(structured, messages)
                message = AIMessage(
                    content="",
                    tool_calls=[{"name": call_name, "args": args, "id": str(uuid4())}],
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
            request = _router_request_text(messages)
            normalized_request = request.casefold()
            lot_code = _extract_lot_code(request)
            is_report_request = any(
                term in normalized_request for term in ("relatório", "relatorio", "envie", "enviar")
            ) and any(term in normalized_request for term in ("relatório", "relatorio"))
            if is_report_request and lot_code:
                return name, {
                    "intent": "preparar relatório do lote",
                    "tasks": [
                        {
                            "task_id": "simulated_farm_report",
                            "agent": "farm",
                            "goal": f"Resolver a origem do relatório do lote {lot_code}.",
                            "brief_sanitized": (
                                "Consultar o lote e obter a origem autorizada do relatório."
                            ),
                            "required_evidence": ["signed_report_source"],
                        },
                        {
                            "task_id": "simulated_secretary_report",
                            "agent": "secretary",
                            "goal": (
                                "Preparar o relatório solicitado a partir da origem autorizada."
                            ),
                            "brief_sanitized": (
                                "Preparar o relatório usando a referência retornada por Fazenda."
                            ),
                            "depends_on": ["simulated_farm_report"],
                            "required_evidence": ["confirmed_email_preparation"],
                        },
                    ],
                }
            if is_report_request and not lot_code:
                return name, {
                    "intent": "preparar relatório",
                    "missing_slots": ["código do lote"],
                }
            agent = "farm"
            if any(term in normalized_request for term in ("consumo", "previsão", "previsao")):
                agent = "analyst"
            elif any(
                term in normalized_request
                for term in (
                    "medicamento",
                    "remédio",
                    "remedio",
                    "vacina",
                    "produto veterinário",
                    "produto veterinario",
                    "catálogo de medicamentos",
                    "catalogo de medicamentos",
                    "bula",
                    "ficha técnica",
                    "ficha tecnica",
                    "princípio ativo",
                    "principio ativo",
                )
            ):
                agent = "doctor"
            elif any(
                term in normalized_request
                for term in (
                    "lei",
                    "contrato",
                    "documento",
                    "juríd",
                    "jurid",
                    "termos",
                    "condições",
                    "condicoes",
                    "faq",
                )
            ):
                agent = "information"
            return name, {
                "intent": "local_simulation",
                "tasks": [
                    {
                        "task_id": "simulated_task",
                        "agent": agent,
                        "goal": request[:4_000]
                        or "Consultar a fonte disponível para a solicitação.",
                        "brief_sanitized": request[:4_000]
                        or "Solicitação enviada no modo simulado.",
                        "required_evidence": ["resultado da ferramenta"],
                    }
                ],
            }
        if name == "SpecialistResult":
            agent = "farm"
            tool_results = [message for message in messages if isinstance(message, ToolMessage)]
            tool_failed = any(
                getattr(message, "status", "success") == "error" for message in tool_results
            )
            if tool_results:
                agent = _agent_for_tool(str(tool_results[-1].name or ""))
            answer = ""
            if tool_failed:
                failed_names = [
                    str(message.name or "ferramenta")
                    for message in tool_results
                    if getattr(message, "status", "success") == "error"
                ]
                answer = (
                    "A consulta falhou em "
                    + ", ".join(dict.fromkeys(failed_names))
                    + "; não há resultado verificável para responder."
                )
            elif tool_results:
                excerpts = []
                for tool_result in tool_results:
                    tool_name = str(tool_result.name or "ferramenta")
                    result = minimize_sensitive_memory_input(str(tool_result.content)).strip()
                    if len(result) > 2_000:
                        result = result[:2_000] + "… [resultado truncado]"
                    excerpts.append(f"Resultado retornado por {tool_name}: {result}")
                answer = "\n".join(excerpts)[:4_000]
            else:
                answer = "Nenhuma consulta foi executada."
            return name, {
                "agent": agent,
                "status": (
                    "error" if tool_failed else "success" if tool_results else "unavailable"
                ),
                "answer": answer,
                "sources": [],
            }
        if name == "OrchestratorResult":
            outcomes = _specialist_input(messages).get("outcomes", ())
            failed_outcome = any(
                isinstance(outcome, dict)
                and isinstance(outcome.get("result"), dict)
                and outcome["result"].get("status") not in {"success", "partial"}
                for outcome in outcomes
            )
            answers = [
                result["answer"].strip()
                for outcome in outcomes
                if isinstance(outcome, dict)
                and isinstance((result := outcome.get("result")), dict)
                and result.get("status") in {"success", "partial"}
                and isinstance(result.get("answer"), str)
                and result["answer"].strip()
            ]
            sources: dict[str, dict[str, Any]] = {}
            for outcome in outcomes:
                result = outcome.get("result") if isinstance(outcome, dict) else None
                result_sources = result.get("sources", ()) if isinstance(result, dict) else ()
                if not isinstance(result_sources, (list, tuple)):
                    continue
                for source in result_sources:
                    if not isinstance(source, dict):
                        continue
                    reference = source.get("reference")
                    if isinstance(reference, str) and reference.strip():
                        sources.setdefault(reference, source)
            return name, {
                "draft_answer": (
                    "Não tenho informação suficiente para responder com segurança agora."
                    if failed_outcome
                    else "\n\n".join(answers)[:6_000]
                    if answers
                    else "Nenhuma ferramenta retornou resultado verificável."
                ),
                "sources": list(sources.values()),
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
        if name == "SessionSummaryDraft":
            return name, _simulated_session_summary(messages)
        return name, {}


_STRUCTURED_NAMES = {
    "RoutePlan",
    "SpecialistResult",
    "OrchestratorResult",
    "JudgeResult",
    "QuestionFormatterResult",
    "UserMemoryDraft",
    "SessionSummaryDraft",
}


def _simulated_session_summary(messages: list[BaseMessage]) -> dict[str, Any]:
    """Gera um resumo estruturalmente válido sem alegar síntese de provider.

    Mantém folga para os demais campos na serialização do contrato."""
    payload_text = _last_human_text(messages)
    try:
        payload = json.loads(payload_text)
    except TypeError, json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    prior = payload.get("summary_to_compact") or payload.get("previous_summary") or {}
    if not isinstance(prior, dict):
        prior = {}
    prior_activity = prior.get("activity")
    prior_activity = prior_activity.strip() if isinstance(prior_activity, str) else ""
    facts = prior.get("important_facts", [])
    decisions = prior.get("decisions", [])
    limitations = prior.get("limitations", [])
    pending = prior.get("pending_activity")

    interactions = payload.get("new_interactions", [])
    user_messages = (
        [
            item.get("content", "").strip()
            for item in interactions
            if isinstance(item, dict)
            and item.get("source") == "conversation_message"
            and item.get("role") == "user"
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        ]
        if isinstance(interactions, list)
        else []
    )
    recent = user_messages[-10:]
    recent_activity = "Solicitações recentes: " + " | ".join(recent) if recent else ""
    activity = " — ".join(part for part in (prior_activity, recent_activity) if part)
    if not activity:
        activity = "Sessão de teste em modo simulado."
    activity = activity[:2_500]

    token_budget = payload.get("token_budget")
    if isinstance(token_budget, int) and token_budget > 0:
        activity = activity[: min(2_500, token_budget * 5)]
    simulated_limitation = (
        "Resumo determinístico do modo simulado; não representa síntese de provider."
    )
    normalized_limitations = (
        [item for item in limitations if isinstance(item, str)]
        if isinstance(limitations, list)
        else []
    )
    return {
        "activity": activity,
        "important_facts": facts[:40] if isinstance(facts, list) else [],
        "decisions": decisions[:40] if isinstance(decisions, list) else [],
        "pending_activity": pending if isinstance(pending, str) else None,
        "limitations": list(dict.fromkeys((*normalized_limitations, simulated_limitation)))[:40],
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


def _router_request_text(messages: list[BaseMessage]) -> str:
    """Retorna a mensagem original do usuário a partir do contrato do roteador."""
    text = _last_human_text(messages)
    try:
        payload = json.loads(text)
    except TypeError, json.JSONDecodeError:
        return text
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return text


def _requested_medicine_name(text: str) -> str | None:
    patterns = (
        r"[\"“]([^\"”]{2,100})[\"”]",
        r"\b(?:medicamento|rem[eé]dio|vacina|produto)\s+"
        r"(?:chamad[oa]\s+)?[\"“]?(.+?)[\"”]?"
        r"(?=\s+(?:e\s+(?:informe|diga|me|qual|consulte|tamb[eé]m)|"
        r"(?:e\s+)?(?:qual|informe|diga|consulte|mostre|busque)\b|para\s+)|[,?.!]|$)",
        r"\b(?:do|da|de)\s+(?:medicamento|rem[eé]dio|vacina|produto)\s+"
        r"[\"“]?(.+?)[\"”]?(?=\s+(?:e\s+)?(?:qual|informe|diga|consulte|mostre|busque)\b|[,?.!]|$)",
        r"\b(?:do|da|de)\s+[\"“]([^\"”]{2,100})[\"”]",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return match.group(1).strip().strip("\"“”' ") or None
    return None


def _agent_for_tool(name: str) -> str:
    if name.startswith("list_medicine") or name == "mock_query_medicine":
        return "doctor"
    if name in {"estimate_medicine_consumption", "mock_estimate_prediction"}:
        return "analyst"
    if name in {"prepare_report", "send_report", "get_report_status", "mock_prepare_report"}:
        return "secretary"
    if "document" in name or "legal" in name or name == "mock_query_documents":
        return "information"
    return "farm"


def _tool_arguments(name: str, specialist_input: dict[str, Any] | None = None) -> dict[str, Any]:
    specialist_input = specialist_input or {}
    if name == "send_report":
        return {}
    if name == "consultar_documental":
        question = str(
            specialist_input.get("goal") or specialist_input.get("brief_sanitized") or ""
        )
        return {"question": question[:4_000] or "Consulte os documentos sintéticos disponíveis."}
    if name == "mock_query_documents":
        question = str(
            specialist_input.get("goal") or specialist_input.get("brief_sanitized") or ""
        )
        return {"pergunta": question[:4_000] or "Consulte os documentos sintéticos disponíveis."}
    if name == "mock_query_farm":
        query = str(specialist_input.get("goal") or specialist_input.get("brief_sanitized") or "")
        return {"consulta": query[:4_000] or "Consulte os dados sintéticos disponíveis."}
    if name == "mock_query_medicine":
        query = str(specialist_input.get("goal") or specialist_input.get("brief_sanitized") or "")
        return {"nome": _requested_medicine_name(query) or query[:500]}
    if name == "mock_estimate_prediction":
        query = str(specialist_input.get("goal") or specialist_input.get("brief_sanitized") or "")
        return {"pergunta": query[:4_000] or "Calcule uma previsão sintética."}
    if name == "mock_prepare_report":
        query = str(specialist_input.get("goal") or specialist_input.get("brief_sanitized") or "")
        return {"tipo": query[:500] or "relatório sintético"}
    if name in {"list_medicines", "list_medicine_documents"}:
        request_text = str(
            specialist_input.get("goal") or specialist_input.get("brief_sanitized") or ""
        )
        medicine_name = _requested_medicine_name(request_text)
        return {"payload": {"brand_name": medicine_name} if medicine_name else {}}
    if name in {"list_properties", "list_herds", "list_lots"}:
        return {"payload": {}}
    if name == "estimate_medicine_consumption":
        return {
            "payload": {
                "as_of": (date.today() - timedelta(days=1)).isoformat(),
                "horizon_days": 7,
            }
        }
    if name == "resolve_report_lot_report_source":
        lot_code = _extract_lot_code(
            str(specialist_input.get("goal") or specialist_input.get("brief_sanitized") or "")
        )
        return {"lot_code": lot_code or "HC-LOTE-076"}
    if name == "prepare_report":
        references = []
        for dependency in specialist_input.get("dependency_results", ()):
            if isinstance(dependency, dict):
                references.extend(dependency.get("report_artifact_refs", ()))
        if references:
            return {
                "report_artifact_ref": references[0],
                "report_title": "Relatório do lote solicitado",
            }
    return {"payload": {}}


def _select_tool(names: list[str], specialist_input: dict[str, Any]) -> str | None:
    if "resolve_report_lot_report_source" in names:
        return "resolve_report_lot_report_source"
    if "prepare_report" in names:
        return "prepare_report"
    if "list_medicines" in names:
        return "list_medicines"
    safe_tools = [name for name in names if name not in {"send_report"}]
    return safe_tools[0] if len(safe_tools) == 1 else None


def _next_tool(
    names: list[str], specialist_input: dict[str, Any], messages: list[BaseMessage]
) -> str | None:
    """No pedido inicial, ainda não existe preparação persistida nesta execução.
    Prepare primeiro; só despache depois de o MCP confirmar status prepared."""
    if specialist_input.get("email_send_authorized") is True and "send_report" in names:
        last_email_result = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, ToolMessage)
                and message.name in {"prepare_report", "send_report"}
            ),
            None,
        )
        if last_email_result is None:
            return "prepare_report" if "prepare_report" in names else None
        if last_email_result.name == "send_report":
            return None
        try:
            preparation = json.loads(str(last_email_result.content))
        except json.JSONDecodeError:
            return None
        return (
            "send_report"
            if isinstance(preparation, dict) and preparation.get("status") == "prepared"
            else None
        )
    request_text = str(
        specialist_input.get("goal") or specialist_input.get("brief_sanitized") or ""
    ).casefold()
    medicine_document_requested = any(
        term in request_text
        for term in (
            "bula",
            "ficha",
            "princípio ativo",
            "principio ativo",
            "ingrediente ativo",
            "dose",
            "dosagem",
            "carência",
            "carencia",
            "contraindicação",
            "contraindicacao",
            "indicação",
            "indicacao",
            "composição",
            "composicao",
        )
    )
    medicine_result = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, ToolMessage) and message.name == "list_medicines"
        ),
        None,
    )
    document_result = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, ToolMessage) and message.name == "list_medicine_documents"
        ),
        None,
    )
    if medicine_document_requested and "list_medicine_documents" in names:
        if medicine_result is None:
            return "list_medicines" if "list_medicines" in names else "list_medicine_documents"
        if getattr(medicine_result, "status", "success") != "error" and document_result is None:
            return "list_medicine_documents"
    prior_tool_result = next(
        (message for message in reversed(messages) if isinstance(message, ToolMessage)),
        None,
    )
    if prior_tool_result is None:
        return _select_tool(names, specialist_input)
    return None


def _specialist_input(messages: list[BaseMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.type != "human":
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _extract_lot_code(value: str) -> str | None:
    match = re.search(r"\b[A-Z0-9]{2,}-[A-Z0-9][A-Z0-9-]{1,18}\b", value, re.IGNORECASE)
    return match.group(0) if match else None
