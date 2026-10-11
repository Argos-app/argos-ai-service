"""Tool local de preparação; identidade, destinatários e envelope vêm do runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.application.email_workflow import EmailWorkflow


class PrepareReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_artifact_ref: UUID = Field(
        description="Referência do relatório fornecida na dependência de Fazenda."
    )
    report_title: str = Field(min_length=1, max_length=200)


def build_email_prepare_tool(workflow: EmailWorkflow | None) -> StructuredTool:
    async def prepare_report(
        report_artifact_ref: UUID,
        report_title: str,
        config: RunnableConfig,
    ) -> str:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, Mapping):
            raise ValueError("contexto protegido da preparação está ausente")
        if workflow is None:
            return "Preparação indisponível; não foi chamado o MCP de e-mail."
        return await workflow.prepare(
            report_artifact_ref=report_artifact_ref,
            report_title=report_title,
            configurable=dict(configurable),
        )

    return StructuredTool.from_function(
        coroutine=prepare_report,
        name="prepare_report",
        description=(
            "Prepara o relatório referenciado em uma dependência de Fazenda. Informe apenas "
            "a referência e um título de até 200 caracteres. Destinatários, usuário e envelope "
            "são recuperados do contexto protegido; esta ferramenta não envia e-mail."
        ),
        args_schema=PrepareReportInput,
    )


__all__ = ["PrepareReportInput", "build_email_prepare_tool"]
