"""Envio e recuperação determinísticos a partir de autorização persistida."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.application.email_workflow import _fingerprint, _payload
from app.application.operation_observation import observe_operation
from app.application.report_artifact_service import validate_report_source
from app.domain.email_preparations import (
    EmailOperationReference,
    EmailSendAuthorization,
    SendAuthorizationOrigin,
)
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.email_tool_client import EmailToolClient
from app.ports.report_artifact_repository import ReportArtifactRepository
from packages.contracts import (
    DispatchStatus,
    PreparedReportStatus,
    RecipientRef,
    ReportStatusRequest,
    ReportStatusResult,
    SendReportRequest,
    SendReportResult,
)
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)


def _recipient_selectors_match(
    selectors: dict[str, Any], recipients: tuple[RecipientRef, ...]
) -> bool:
    def normalize(value: str) -> str:
        apostrophes = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})
        return value.strip().translate(apostrophes).casefold()

    if len(selectors) != len(recipients):
        return False
    unmatched = set(range(len(recipients)))
    for selector in selectors.values():
        if not isinstance(selector, dict):
            return False
        kind = selector.get("kind")
        value = selector.get("value")
        if kind not in {"name", "email"} or not isinstance(value, str):
            return False
        normalized = normalize(value)
        matches = [
            index
            for index in unmatched
            if normalized == normalize(
                recipients[index].name if kind == "name" else recipients[index].email
            )
        ]
        if len(matches) != 1:
            return False
        unmatched.remove(matches[0])
    return not unmatched


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
        email_client: EmailToolClient,
        preparations: EmailPreparationRepository,
        report_artifacts: ReportArtifactRepository,
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        if email_client.service_name != "email":
            raise ValueError("EmailDispatchWorkflow exige um cliente MCP de e-mail")
        self._email_client = email_client
        self._preparations = preparations
        self._report_artifacts = report_artifacts
        self._observation_collector = observation_collector

    @staticmethod
    def _identity(configurable: dict[str, Any]) -> tuple[UUID, UUID]:
        try:
            return UUID(str(configurable["argos_user_id"])), UUID(str(configurable["session_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "identidade autenticada para a operação de e-mail está ausente"
            ) from exc

    async def send(self, *, configurable: dict[str, Any]) -> str:
        return await self._observe_workflow(
            "email.dispatch.send",
            self._send,
            configurable=configurable,
        )

    async def _send(self, *, configurable: dict[str, Any]) -> str:
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
            authorization = await self._authorize_prepared_confirmation(
                principal_user_id=principal_user_id,
                session_id=session_id,
                source_interaction_id=source_interaction_id,
                configurable=configurable,
            )

        operation_reference = await self._preparations.get_send_operation_reference(
            authorization_id=authorization.authorization_id,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        if operation_reference is not None:
            return await self._refresh_status(authorization, operation_reference, principal_user_id)

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
        await self._preparations.save_send_operation_reference(
            EmailOperationReference(
                authorization_id=authorization.authorization_id,
                principal_user_id=principal_user_id,
                session_id=session_id,
                operation_id=result.operation_id,
                recorded_at=datetime.now(UTC),
            )
        )
        return _safe_result(result)

    async def _authorize_prepared_confirmation(
        self,
        *,
        principal_user_id: UUID,
        session_id: UUID,
        source_interaction_id: UUID,
        configurable: dict[str, Any],
    ) -> EmailSendAuthorization:
        if configurable.get("argos_send_intent_requested") is not True:
            raise ValueError("não há autorização explícita para este envio")

        try:
            selectors = configurable["recipient_selectors"]
            property_ids = tuple(
                UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("destinatários ou escopo autorizado ausentes") from exc
        if not isinstance(selectors, dict) or not selectors or not property_ids:
            raise ValueError("destinatários ou escopo autorizado ausentes")

        preparations = await self._preparations.list_prepared_for_session(
            principal_user_id=principal_user_id,
            session_id=session_id,
            limit=100,
        )
        now = datetime.now(UTC)
        candidates = tuple(
            item
            for item in preparations
            if item.prepared_report.status == PreparedReportStatus.PREPARED
            and now < item.prepared_report.expires_at
        )
        if len(candidates) != 1:
            raise ValueError("não existe uma única preparação válida para confirmar nesta sessão")
        preparation = candidates[0]
        prepared = preparation.prepared_report
        if not _recipient_selectors_match(selectors, prepared.recipients):
            raise ValueError("destinatários confirmados não correspondem à preparação")

        existing_authorizations = await self._preparations.list_send_authorizations(
            principal_user_id=principal_user_id,
            session_id=session_id,
            limit=100,
        )
        if any(item.report_id == prepared.report_id for item in existing_authorizations):
            raise ValueError("a preparação já tem uma autorização de envio")

        artifact = await self._report_artifacts.get_owned(
            reference=preparation.report_artifact_ref,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        validate_report_source(
            artifact.envelope,
            principal_user_id=principal_user_id,
            lot_id=artifact.envelope.report.requested_lot.lot_id,
            authorized_property_ids=property_ids,
            now=now,
        )

        authorization = EmailSendAuthorization(
            authorization_id=uuid4(),
            principal_user_id=principal_user_id,
            session_id=session_id,
            source_interaction_id=source_interaction_id,
            preparation_reference=preparation.reference,
            report_id=prepared.report_id,
            request_fingerprint=preparation.request_fingerprint,
            prepared_fingerprint=_fingerprint(prepared.model_dump(mode="json")),
            recipients_fingerprint=_fingerprint(
                [recipient.model_dump(mode="json") for recipient in prepared.recipients]
            ),
            origin=SendAuthorizationOrigin.EXPLICIT_REQUEST,
            idempotency_key=str(uuid4()),
            authorized_at=now,
            expires_at=prepared.expires_at,
        )
        return await self._preparations.reserve_send_authorization(authorization)

    async def status(self, *, authorization_id: UUID | None, configurable: dict[str, Any]) -> str:
        return await self._observe_workflow(
            "email.dispatch.status",
            self._status,
            authorization_id=authorization_id,
            configurable=configurable,
        )

    async def _status(self, *, authorization_id: UUID | None, configurable: dict[str, Any]) -> str:
        principal_user_id, session_id = self._identity(configurable)
        if authorization_id is None:
            authorizations = await self._preparations.list_send_authorizations(
                principal_user_id=principal_user_id,
                session_id=session_id,
            )
            if not authorizations:
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "Não encontrei ações de envio nesta sessão.",
                    },
                    ensure_ascii=False,
                )
            if len(authorizations) > 1:
                actions = await self._action_options(
                    authorizations, principal_user_id=principal_user_id, session_id=session_id
                )
                return json.dumps(
                    {
                        "status": "needs_action_selection",
                        "actions": actions,
                        "message": "Há mais de uma ação; peça ao usuário para indicar qual delas.",
                    },
                    ensure_ascii=False,
                )
            authorization = authorizations[0]
        else:
            authorization = await self._preparations.get_send_authorization_by_id(
                authorization_id=authorization_id,
                principal_user_id=principal_user_id,
                session_id=session_id,
            )
            if authorization is None:
                raise ValueError("ação de envio não encontrada para o usuário autenticado")
        operation_reference = await self._preparations.get_send_operation_reference(
            authorization_id=authorization.authorization_id,
            principal_user_id=principal_user_id,
            session_id=session_id,
        )
        if operation_reference is None:
            return _safe_unconfirmed()
        return await self._refresh_status(authorization, operation_reference, principal_user_id)

    async def recover_interaction_outcome(self, *, configurable: dict[str, Any]) -> str | None:
        return await self._observe_workflow(
            "email.dispatch.recovery",
            self._recover_interaction_outcome,
            configurable=configurable,
            recovered=True,
        )

    async def _recover_interaction_outcome(self, *, configurable: dict[str, Any]) -> str | None:
        """Return a deterministic public summary for a send linked to this request.

        This only reads the authorization/reference already persisted by the main API and asks
        the MCP for its current status. It never initiates or retries a dispatch.
        """
        try:
            principal_user_id, session_id = self._identity(configurable)
            source_interaction_id = UUID(
                str(configurable["argos_send_intent_source_interaction_id"])
            )
            authorization = await self._preparations.get_send_authorization(
                principal_user_id=principal_user_id,
                session_id=session_id,
                source_interaction_id=source_interaction_id,
            )
            if authorization is None:
                return None
            reference = await self._preparations.get_send_operation_reference(
                authorization_id=authorization.authorization_id,
                principal_user_id=principal_user_id,
                session_id=session_id,
            )
            if reference is None:
                return None
            result = json.loads(
                await self._refresh_status(authorization, reference, principal_user_id)
            )
        except Exception:
            return "O MCP não confirmou o estado do envio; nenhum novo envio foi solicitado."

        summaries = {
            DispatchStatus.PENDING.value: "O envio está em processamento.",
            DispatchStatus.ACCEPTED.value: (
                "O MCP confirmou o recebimento do envio; isso não confirma a entrega."
            ),
            DispatchStatus.PARTIAL.value: "O MCP confirmou um envio parcial.",
            DispatchStatus.FAILED.value: "O MCP confirmou que o envio falhou.",
            DispatchStatus.UNKNOWN.value: (
                "O MCP ainda não confirmou o estado do envio; nenhum novo envio foi solicitado."
            ),
        }
        return summaries.get(
            result.get("status"),
            "O MCP não confirmou o estado do envio; nenhum novo envio foi solicitado.",
        )

    async def _observe_workflow(self, name, operation, *, recovered=False, **kwargs):
        if self._observation_collector is None:
            return await operation(**kwargs)
        async with observe_operation(
            self._observation_collector,
            operation_kind=OperationKind.WORKFLOW,
            operation_name=name,
            operation_phase=(OperationPhase.RECOVERY if recovered else OperationPhase.EXECUTION),
            observation_level=ObservationLevel.LOCAL_OPERATION,
            error_origin=ErrorOrigin.APPLICATION,
        ) as recorder:
            result = await operation(**kwargs)
            code = None
            confirmed = None
            reported_recovered = None
            if isinstance(result, str):
                try:
                    payload = json.loads(result)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    raw_code = payload.get("status")
                    if isinstance(raw_code, str) and len(raw_code) <= 120:
                        code = raw_code
                    raw_confirmed = payload.get("confirmed")
                    confirmed = raw_confirmed if isinstance(raw_confirmed, bool) else None
                    raw_recovered = payload.get("recovered")
                    reported_recovered = raw_recovered if isinstance(raw_recovered, bool) else None
            if recovered:
                code = code or ("no_prior_operation" if result is None else "recovery_result")
            recorder.finish(
                result=(
                    ResultMetadata(
                        source="application",
                        code=code,
                        confirmed=confirmed,
                        recovered=(
                            reported_recovered
                            if reported_recovered is not None
                            else True
                            if recovered and result is not None
                            else None
                        ),
                    )
                    if code or confirmed is not None or reported_recovered is not None
                    else None
                )
            )
            return result

    async def _action_options(self, authorizations, *, principal_user_id, session_id):
        actions = []
        for authorization in authorizations:
            operation_reference = await self._preparations.get_send_operation_reference(
                authorization_id=authorization.authorization_id,
                principal_user_id=principal_user_id,
                session_id=session_id,
            )
            if operation_reference is None:
                status = "not_confirmed"
            else:
                status = json.loads(
                    await self._refresh_status(
                        authorization, operation_reference, principal_user_id
                    )
                )["status"]
            actions.append(
                {
                    "action_ref": str(authorization.authorization_id),
                    "authorized_at": authorization.authorized_at.isoformat(),
                    "status": status,
                }
            )
        return actions

    async def _refresh_status(
        self, authorization, operation_reference, principal_user_id: UUID
    ) -> str:
        request = ReportStatusRequest(
            operation_id=operation_reference.operation_id,
            requested_by=principal_user_id,
        )
        try:
            response = await self._email_client.call_tool(
                "get_report_status", {"payload": request.model_dump(mode="json")}
            )
            result = ReportStatusResult.model_validate(_payload(response))
            self._validate_result(result, authorization.report_id)
        except Exception:
            return _safe_unconfirmed()
        return _safe_result(result, recovered=True)

    @staticmethod
    def _validate_result(result: SendReportResult, report_id: UUID) -> None:
        if result.report_id != report_id:
            raise ValueError("MCP retornou resultado para outro relatório")


__all__ = ["EmailDispatchWorkflow"]
