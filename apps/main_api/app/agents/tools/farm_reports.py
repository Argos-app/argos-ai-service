"""Ferramenta de Fazenda para resolver lote antes de solicitar sua origem."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.report_artifact_service import validate_report_source
from app.domain.report_artifacts import ReportArtifact
from app.ports.data_client import DataClient, DataReportSourceUnavailable
from app.ports.report_artifact_repository import ReportArtifactRepository
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


class ReportLotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_code: str = Field(min_length=1, max_length=20, description="Código exato do lote.")
    herd_id: UUID | None = Field(default=None, description="Use somente se já identificado.")
    property_id: UUID | None = Field(default=None, description="Use somente se já identificada.")


def build_farm_report_tool(
    data_client: DataClient,
    artifact_repository: ReportArtifactRepository | None = None,
    signature_checker: Callable[[bytes, str, str], bool] | None = None,
) -> StructuredTool:
    async def resolve_report_source(
        lot_code: str,
        config: RunnableConfig,
        herd_id: UUID | None = None,
        property_id: UUID | None = None,
    ) -> str:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, Mapping):
            raise ValueError("contexto confiável de execução ausente")
        try:
            principal_user_id = UUID(str(configurable["argos_user_id"]))
            firebase_uid = str(configurable["argos_firebase_uid"])
            request_id = UUID(str(configurable["argos_request_id"]))
            session_id = UUID(str(configurable["session_id"]))
            interaction_id = UUID(str(configurable["interaction_id"]))
            task_id = str(configurable["argos_task_id"])
            authorized_property_ids = tuple(
                UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("identidade e escopo autorizados são obrigatórios") from exc
        if property_id is not None and property_id not in authorized_property_ids:
            raise ValueError("propriedade solicitada está fora do escopo autorizado")

        resolution = await data_client.resolve_report_lot(
            lot_code=lot_code,
            firebase_uid=firebase_uid,
            principal_user_id=principal_user_id,
            authorized_property_ids=authorized_property_ids,
            request_id=request_id,
            herd_id=herd_id,
            property_id=property_id,
        )
        if not resolution.candidates:
            return json.dumps(
                {"status": "not_found", "lot_code": lot_code},
                ensure_ascii=False,
            )
        if resolution.unique_candidate is None:
            return json.dumps(
                {
                    "status": "needs_clarification",
                    "candidates": [
                        {
                            "lot_code": item.lot_code,
                            "herd_name": item.herd_name,
                            "property_name": item.property_name,
                        }
                        for item in resolution.candidates
                    ],
                    "more_candidates": resolution.has_more,
                },
                ensure_ascii=False,
            )

        candidate = resolution.unique_candidate
        try:
            envelope = await data_client.get_lot_report_source(
                lot_id=candidate.lot_id,
                property_id=candidate.property_id,
                firebase_uid=firebase_uid,
                principal_user_id=principal_user_id,
                authorized_property_ids=authorized_property_ids,
                request_id=request_id,
            )
        except DataReportSourceUnavailable:
            return json.dumps(
                {
                    "status": "report_source_unavailable",
                    "message": (
                        "O MCP de dados ainda não publica o envelope assinado "
                        "necessário para preparar este relatório."
                    ),
                },
                ensure_ascii=False,
            )
        if signature_checker is None:
            raise ValueError("verificador da assinatura da origem de relatório não configurado")
        validate_report_source(
            envelope,
            principal_user_id=principal_user_id,
            lot_id=candidate.lot_id,
            authorized_property_ids=authorized_property_ids,
            signature_checker=signature_checker,
        )
        if artifact_repository is None:
            return json.dumps(
                {
                    "status": "report_source_storage_unavailable",
                    "message": "Não foi possível armazenar a origem em contexto confiável.",
                },
                ensure_ascii=False,
            )
        artifact = ReportArtifact(
            reference=uuid4(),
            envelope=envelope,
            principal_user_id=principal_user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            task_id=task_id,
            created_at=datetime.now(UTC),
        )
        await artifact_repository.save(artifact)
        return json.dumps(
            {
                "status": "report_source_ready",
                "report_artifact_ref": str(artifact.reference),
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        coroutine=resolve_report_source,
        name="resolve_report_lot_report_source",
        description=(
            "Resolve o lote informado dentro das propriedades autorizadas e solicita "
            "a origem assinada do relatório. Se houver mais de uma opção, devolve "
            "candidatos para esclarecimento; nunca escolhe um homônimo."
        ),
        args_schema=ReportLotInput,
    )


__all__ = [
    "ReportLotInput",
    "build_farm_report_tool",
    "validate_report_source",
]
