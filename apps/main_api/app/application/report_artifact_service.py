"""Validação contextual de origens assinadas de relatório."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from packages.contracts import LotReportEnvelope


class ReportSourceValidationError(ValueError):
    """A origem não corresponde à identidade, ao lote ou ao prazo confiável."""


class ReportSourceExpiredError(ReportSourceValidationError):
    """A validade da origem assinada terminou."""


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
        if current_time.tzinfo is not None:
            raise ReportSourceExpiredError("envelope expirado")
        raise ReportSourceValidationError("relógio sem timezone")
    canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode("utf-8")
    expected_hash = f"sha256:{sha256(canonical).hexdigest()}"
    if envelope.payload_hash != expected_hash:
        raise ReportSourceValidationError("hash do envelope não corresponde ao conteúdo")
    return envelope
