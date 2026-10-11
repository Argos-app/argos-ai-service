"""Ferramenta de Fazenda para resolver lote antes de solicitar sua origem."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from app.ports.data_client import DataClient, DataReportSourceUnavailable
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from packages.contracts import LotReportEnvelope


class ReportSourceValidationError(ValueError):
    """A origem não corresponde à identidade, ao lote ou ao prazo confiável."""


class ReportLotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_code: str = Field(min_length=1, max_length=20, description="Código exato do lote.")
    herd_id: UUID | None = Field(default=None, description="Use somente se já identificado.")
    property_id: UUID | None = Field(default=None, description="Use somente se já identificada.")


def validate_report_source(
    value: object,
    *,
    principal_user_id: UUID,
    lot_id: UUID,
    authorized_property_ids: tuple[UUID, ...],
    now: datetime | None = None,
) -> LotReportEnvelope:
    """Valida claims, escopo, validade e hash antes de qualquer uso da origem."""
    try:
        envelope = (
            value
            if isinstance(value, LotReportEnvelope)
            else LotReportEnvelope.model_validate(value)
        )
    except (TypeError, ValueError) as exc:
        raise ReportSourceValidationError("schema do envelope de origem inválido") from exc
    if envelope.principal_user_id != principal_user_id:
        raise ReportSourceValidationError("solicitante da origem não corresponde ao usuário")
    if envelope.report.requested_lot.lot_id != lot_id:
        raise ReportSourceValidationError("a origem não corresponde ao lote selecionado")
    selected_property_id = envelope.report.requested_lot.property_id
    if (
        selected_property_id not in authorized_property_ids
        or selected_property_id not in envelope.authorized_property_ids
    ):
        raise ReportSourceValidationError("propriedade da origem fora do escopo autorizado")
    if envelope.valid_until.tzinfo is None:
        raise ReportSourceValidationError("validade do envelope deve incluir timezone")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time >= envelope.valid_until:
        raise ReportSourceValidationError("envelope expirado ou relógio sem timezone")
    canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode("utf-8")
    expected_hash = f"sha256:{sha256(canonical).hexdigest()}"
    if envelope.payload_hash != expected_hash:
        raise ReportSourceValidationError("hash do envelope não corresponde ao conteúdo")
    return envelope


def build_farm_report_tool(data_client: DataClient) -> StructuredTool:
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
            request_id = UUID(str(configurable["argos_request_id"]))
            authorized_property_ids = tuple(
                UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("identidade e escopo autorizados são obrigatórios") from exc
        if property_id is not None and property_id not in authorized_property_ids:
            raise ValueError("propriedade solicitada está fora do escopo autorizado")

        resolution = await data_client.resolve_report_lot(
            lot_code=lot_code,
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
                principal_user_id=principal_user_id,
                authorized_property_ids=authorized_property_ids,
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
        validate_report_source(
            envelope,
            principal_user_id=principal_user_id,
            lot_id=candidate.lot_id,
            authorized_property_ids=authorized_property_ids,
        )
        return json.dumps(
            {
                "status": "report_source_storage_unavailable",
                "message": (
                    "A origem não foi entregue ao agente: o armazenamento confiável "
                    "de artefatos ainda não está disponível."
                ),
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
    "ReportSourceValidationError",
    "build_farm_report_tool",
    "validate_report_source",
]
