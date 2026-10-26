"""Tool de Informações que encaminha perguntas ao serviço documental A2A."""

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BeforeValidator, Field, create_model

from app.infrastructure.a2a.client import DocumentA2AClient
from packages.contracts.a2a import DocumentSkill


def _normalize_optional_value(value: Any) -> Any:
    if isinstance(value, str) and value.strip().casefold() in {"", "none", "null"}:
        return None
    return value


def build_document_a2a_tool(
    client: DocumentA2AClient,
    *,
    skill: DocumentSkill = DocumentSkill.DOCUMENTAL,
) -> StructuredTool:
    """Cria uma tool fixa com identidade obtida do contexto autenticado."""

    tool_name = "consultar_legal" if skill is DocumentSkill.LEGAL else "consultar_documental"
    schema_fields = {"question": (str, Field(min_length=1, max_length=4_000))}
    if skill is DocumentSkill.LEGAL:
        schema_fields.update(
            {
                "jurisdiction": (
                    Annotated[str | None, BeforeValidator(_normalize_optional_value)],
                    Field(
                        default=None,
                        min_length=1,
                        max_length=120,
                        description="Jurisdição citada pelo usuário, por exemplo BR.",
                    ),
                ),
                "reference_date": (
                    Annotated[date | None, BeforeValidator(_normalize_optional_value)],
                    Field(default=None, description="Data de referência em formato YYYY-MM-DD."),
                ),
            }
        )
    schema = create_model(f"{tool_name.title().replace('_', '')}Input", **schema_fields)

    async def invoke(
        question: str,
        config: RunnableConfig,
        jurisdiction: str | None = None,
        reference_date: date | None = None,
    ) -> str:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, Mapping):
            raise ValueError("contexto autenticado da consulta documental está ausente")
        user_id = configurable.get("argos_user_id")
        if not user_id:
            raise ValueError("identidade autenticada é obrigatória para consultar Documental")
        return await client.ask(
            question,
            user_id=user_id,
            skill=skill,
            jurisdiction=jurisdiction,
            reference_date=reference_date,
        )

    return StructuredTool.from_function(
        coroutine=invoke,
        name=tool_name,
        description=(
            "Consulta o serviço A2A para buscar evidências em "
            + (
                "leis. Informe jurisdiction quando a jurisdição estiver explícita na pergunta; "
                "passe reference_date somente quando o usuário pedir uma data específica."
                if skill is DocumentSkill.LEGAL
                else "termos, condições e políticas globais."
            )
            + " Retorna status, limitações e referências da resposta A2A."
        ),
        args_schema=schema,
    )
