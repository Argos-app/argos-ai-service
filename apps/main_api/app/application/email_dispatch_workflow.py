"""Envio e recuperação determinísticos a partir de autorização persistida."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.application.email_workflow import _fingerprint, _payload
from app.application.report_artifact_service import validate_report_source
from app.domain.email_preparations import EmailSendReceipt
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.report_artifact_repository import ReportArtifactRepository
from packages.contracts import (
    DispatchStatus,
    PreparedReportStatus,
    ReportStatusRequest,
    ReportStatusResult,
    SendReportRequest,
    SendReportResult,
)


def _safe_result(result: SendReportResult, *, recovered: bool = False) -> str:
    labels = {
        DispatchStatus.PENDING: "O envio está em processamento.",
        DispatchStatus.ACCEPTED: "O MCP confirmou o recebimento do envio.",
        DispatchStatus.PARTIAL: "O MCP confirmou um envio parcial.",
        DispatchStatus.FAILED: "O MCP confirmou que o envio falhou.",
        DispatchStatus.UNKNOWN: "O estado do envio ainda não foi confirmado.",
    }
    return json.dumps(
        {
            "status": result.status.value,
            "report_id": str(result.report_id),
            "recovered": recovered,
            "message": labels[result.status],
        },
        ensure_ascii=False,
    )


def _safe_unconfirmed() -> str:
    return json.dumps(
        {
            "status": DispatchStatus.UNKNOWN.value,
            "confirmed": False,
            "message": "O resultado do envio não foi confirmado; não solicite outro envio.",
        },
        ensure_ascii=False,
    )


class EmailDispatchWorkflow:
    """Aplica identidade, autorização, escopo e idempotência fora do agente."""

    def __init__(
        self,
        *,
        email_client: Any,
        preparations: EmailPreparationRepository,
        report_artifacts: ReportArtifactRepository,
    ) -> None:
        if email_client.service_name != "email":
            raise ValueError("EmailDispatchWorkflow exige um cliente MCP de e-mail")
        self._email_client = email_client
        self._preparations = preparations
        self._report_artifacts = report_artifacts

    @staticmethod
    def _identity(configurable: dict[str, Any]) -> tuple[UUID, UUID]:
        try:
            return UUID(str(configurable["argos_user_id"])), UUID(str(configurable["session_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "identidade autenticada para a operação de e-mail está ausente"
            ) from exc

    async def send(self, *, configurable: dict[str, Any]) -> str:
        principal_user_id, session_id = self._identity(configurable)
        try:
            source_interaction_id = UUID(
                str(configurable["argos_send_intent_source_interaction_id"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("origem autenticada da autorização de envio está ausente") from exc
        authorization = await self._preparations.get_send_authorization(
            principal_user_id=principal_user_id,
            session_id=session_id,
            source_interaction_id=source_interaction_id,
        )
        if authorization is None:
            raise ValueError("não há autorização persistida para este pedido de envio")

        receipt = await self._preparations.get_send_receipt(
            authorization_id=authorization.authorization_id,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        if receipt is not None:
            return await self._refresh_status(authorization, receipt, principal_user_id)

        now = datetime.now(UTC)
        if now >= authorization.expires_at:
            raise ValueError("a autorização para iniciar o envio expirou")
        preparation = await self._preparations.get_by_reference(
            reference=authorization.preparation_reference,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        if preparation is None:
            raise ValueError("preparação autorizada não está mais disponível")
        prepared = preparation.prepared_report
        if (
            preparation.request_fingerprint != authorization.request_fingerprint
            or prepared.report_id != authorization.report_id
            or prepared.requested_by != principal_user_id
            or prepared.status != PreparedReportStatus.PREPARED
            or now >= prepared.expires_at
            or _fingerprint(prepared.model_dump(mode="json")) != authorization.prepared_fingerprint
            or _fingerprint([item.model_dump(mode="json") for item in prepared.recipients])
            != authorization.recipients_fingerprint
        ):
            raise ValueError("preparação não corresponde à autorização válida de envio")

        property_ids = configurable.get("argos_authorized_property_ids")
        if not isinstance(property_ids, (list, tuple)) or not property_ids:
            raise ValueError("escopo atual de propriedades autorizadas está ausente")
        artifact = await self._report_artifacts.get_owned(
            reference=preparation.report_artifact_ref,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        validate_report_source(
            artifact.envelope,
            principal_user_id=principal_user_id,
            lot_id=artifact.envelope.report.requested_lot.lot_id,
            authorized_property_ids=tuple(UUID(str(value)) for value in property_ids),
            now=now,
        )

        request = SendReportRequest(
            report_id=prepared.report_id,
            requested_by=principal_user_id,
            idempotency_key=authorization.idempotency_key,
        )
        try:
            response = await self._email_client.call_tool(
                "send_report", {"payload": request.model_dump(mode="json")}
            )
        except Exception:
            # A nova tentativa recupera a mesma operação usando esta autorização e chave.
            return _safe_unconfirmed()
        result = SendReportResult.model_validate(_payload(response))
        self._validate_result(result, authorization.report_id)
        persisted = await self._preparations.save_send_receipt(
            EmailSendReceipt(
                authorization_id=authorization.authorization_id,
                principal_user_id=principal_user_id,
                session_id=session_id,
                result=result,
                recorded_at=datetime.now(UTC),
            )
        )
        return _safe_result(persisted.result)

    async def status(self, *, authorization_id: UUID, configurable: dict[str, Any]) -> str:
        principal_user_id, session_id = self._identity(configurable)
        authorization = await self._preparations.get_send_authorization_by_id(
            authorization_id=authorization_id,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        if authorization is None:
            raise ValueError("ação de envio não encontrada para o usuário autenticado")
        receipt = await self._preparations.get_send_receipt(
            authorization_id=authorization.authorization_id,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        if receipt is None:
            return _safe_unconfirmed()
        return await self._refresh_status(authorization, receipt, principal_user_id)

    async def recent_actions(self, *, configurable: dict[str, Any]) -> str:
        """Projeta ações próprias sem expor documentos, chaves ou operation IDs."""
        principal_user_id, session_id = self._identity(configurable)
        authorizations = await self._preparations.list_send_authorizations(
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        actions = []
        for authorization in authorizations:
            receipt = await self._preparations.get_send_receipt(
                authorization_id=authorization.authorization_id,
                principal_user_id=principal_user_id,
                session_id=session_id,
            )
            actions.append(
                {
                    "action_ref": str(authorization.authorization_id),
                    "authorized_at": authorization.authorized_at.isoformat(),
                    "status": receipt.result.status.value if receipt else "not_confirmed",
                }
            )
        return json.dumps({"actions": actions}, ensure_ascii=False)

    async def _refresh_status(self, authorization, receipt, principal_user_id: UUID) -> str:
        request = ReportStatusRequest(
            operation_id=receipt.result.operation_id,
            requested_by=principal_user_id,
        )
        try:
            response = await self._email_client.call_tool(
                "get_report_status", {"payload": request.model_dump(mode="json")}
            )
            result = ReportStatusResult.model_validate(_payload(response))
            self._validate_result(result, authorization.report_id)
        except Exception:
            return _safe_result(receipt.result, recovered=True)
        updated = await self._preparations.save_send_receipt(
            EmailSendReceipt(
                authorization_id=authorization.authorization_id,
                principal_user_id=receipt.principal_user_id,
                session_id=receipt.session_id,
                result=result,
                recorded_at=datetime.now(UTC),
            )
        )
        return _safe_result(updated.result, recovered=True)

    @staticmethod
    def _validate_result(result: SendReportResult, report_id: UUID) -> None:
        if result.report_id != report_id:
            raise ValueError("MCP retornou resultado para outro relatório")


__all__ = ["EmailDispatchWorkflow"]
