"""Tool local de preparação; identidade, destinatários e envelope vêm do runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.application.email_dispatch_workflow import EmailDispatchWorkflow
    from app.application.email_workflow import EmailWorkflow


class PrepareReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_artifact_ref: UUID = Field(
        description="Referência do relatório fornecida na dependência de Fazenda."
    )
    report_title: str = Field(min_length=1, max_length=200)


class SendReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetReportStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ref: UUID | None = Field(
        default=None,
        description=(
            "Referência opaca retornada por uma consulta anterior quando há várias ações. "
            "Não solicite ao usuário que informe UUIDs."
        ),
    )


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


def build_email_send_tool(workflow: EmailDispatchWorkflow | None) -> StructuredTool:
    async def send_report(config: RunnableConfig) -> str:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, Mapping):
            raise ValueError("contexto protegido do envio está ausente")
        if workflow is None:
            return "Envio indisponível; nenhuma chamada foi feita ao MCP de e-mail."
        return await workflow.send(configurable=dict(configurable))

    return StructuredTool.from_function(
        coroutine=send_report,
        name="send_report",
        description=(
            "Solicita o envio somente quando o runtime encontrou autorização explícita "
            "válida para a preparação. Não aceita destinatários, relatório, usuário ou chave "
            "idempotente como argumentos."
        ),
        args_schema=SendReportInput,
    )


def build_email_status_tool(workflow: EmailDispatchWorkflow | None) -> StructuredTool:
    async def get_report_status(
        action_ref: UUID | None = None,
        *,
        config: RunnableConfig,
    ) -> str:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, Mapping):
            raise ValueError("contexto protegido da consulta de e-mail está ausente")
        if workflow is None:
            return "Consulta indisponível; nenhum status foi solicitado ao MCP de e-mail."
        return await workflow.status(
            authorization_id=action_ref,
            configurable=dict(configurable),
        )

    return StructuredTool.from_function(
        coroutine=get_report_status,
        name="get_report_status",
        description=(
            "Consulta o estado atual no MCP para uma ação do usuário autenticado. "
            "Sem action_ref, usa a única ação da sessão ou retorna opções opacas se houver "
            "mais de uma; nunca peça UUID interno ao usuário."
        ),
        args_schema=GetReportStatusInput,
    )


__all__ = [
    "GetReportStatusInput",
    "PrepareReportInput",
    "SendReportInput",
    "build_email_prepare_tool",
    "build_email_send_tool",
    "build_email_status_tool",
]
