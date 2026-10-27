"""Adapter da porta de conversa para um grafo LangGraph compilado."""

from typing import Any

from app.graph.workflow import build_session_config, executar_fluxo, executar_resposta


class LangGraphConversationFlow:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def execute(self, message: str, **kwargs: Any) -> dict[str, Any]:
        return await executar_fluxo(self._graph, message, **kwargs)

    async def resume(self, answer: str, **kwargs: Any) -> dict[str, Any]:
        return await executar_resposta(self._graph, answer, **kwargs)

    async def read_state(self, session_id: str) -> dict[str, Any]:
        snapshot = await self._graph.aget_state({"configurable": {"thread_id": session_id}})
        return getattr(snapshot, "values", {})

    async def update_state(
        self,
        session_id: str,
        config: dict[str, Any],
        values: dict[str, Any],
        *,
        unavailable_message: str,
    ) -> None:
        updater = getattr(self._graph, "aupdate_state", None)
        if not callable(updater):
            raise RuntimeError(unavailable_message)
        await updater(build_session_config(session_id, config), values)
