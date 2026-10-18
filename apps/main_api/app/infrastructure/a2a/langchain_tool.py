"""Tool de Informações que encaminha perguntas ao serviço documental A2A."""

from collections.abc import Mapping

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from app.infrastructure.a2a.client import DocumentA2AClient


def build_document_a2a_tool(client: DocumentA2AClient) -> StructuredTool:
    """Cria uma tool fixa com identidade obtida do contexto autenticado."""

    schema = create_model(
        "ConsultarDocumentalInput",
        question=(str, Field(min_length=1, max_length=4_000)),
    )

    async def invoke(question: str, config: RunnableConfig) -> str:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, Mapping):
            raise ValueError("contexto autenticado da consulta documental está ausente")
        user_id = configurable.get("argos_user_id")
        if not user_id:
            raise ValueError("identidade autenticada é obrigatória para consultar Documental")
        return await client.ask(question, user_id=user_id)

    return StructuredTool.from_function(
        coroutine=invoke,
        name="consultar_documental",
        description=(
            "Consulta o serviço Documental para buscar evidências em termos e condições "
            "globais. Retorna status, limitações e referências da resposta A2A."
        ),
        args_schema=schema,
    )
