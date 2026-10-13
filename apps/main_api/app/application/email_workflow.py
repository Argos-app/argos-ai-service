"""Coordena preparação segura de relatório sem entregar payload de negócio ao LLM."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from app.application.operation_observation import observe_operation
from app.application.report_artifact_service import validate_report_source
from app.domain.email_preparations import (
    EmailPreparation,
    EmailSendAuthorization,
    PendingRecipientOption,
    PendingRecipientResolution,
    SendAuthorizationOrigin,
)
from app.ports.email_preparation_repository import (
    EmailPreparationRepository,
    EmailSendAuthorizationConflictError,
)
from app.ports.email_tool_client import EmailToolClient
from app.ports.report_artifact_repository import ReportArtifactRepository
from app.session_config import CLARIFICATION_TTL_SECONDS
from packages.contracts import (
    PreparedReport,
    PreparedReportStatus,
    PrepareReportRequest,
    RecipientResolutionStatus,
    RecipientSelector,
)
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)


def _payload(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    text_blocks = [
        block.text
        for block in getattr(result, "content", ())
        if isinstance(getattr(block, "text", None), str)
    ]
    if len(text_blocks) != 1:
        raise ValueError("resposta de preparação ausente ou ambígua")
    try:
        return json.loads(text_blocks[0])
    except json.JSONDecodeError as exc:
        raise ValueError("resposta de preparação não contém JSON válido") from exc


def _minimal_result(prepared: PreparedReport, *, reused: bool) -> str:
    if prepared.status == PreparedReportStatus.PREPARED:
        message = "Relatório preparado."
    elif prepared.status == PreparedReportStatus.NEEDS_RECIPIENTS:
        message = "Destinatários precisam ser resolvidos; nenhum envio foi feito."
    else:
        message = "O MCP recusou a preparação; nenhum envio foi feito."
    return json.dumps(
        {
            "status": prepared.status.value,
            "report_id": str(prepared.report_id),
            "expires_at": prepared.expires_at.isoformat(),
            "recipient_count": len(prepared.recipients),
            "reused": reused,
            "message": message,
        },
        ensure_ascii=False,
    )


def _masked_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    return f"{local[:1] or '*'}***@{domain}" if separator else "contato mascarado"


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


async def _recipient_question_result(
    *,
    prepared: PreparedReport,
    principal_user_id: UUID,
    session_id: UUID,
    interaction_id: UUID,
    report_artifact_ref: UUID,
    repository: EmailPreparationRepository,
    now: datetime,
) -> str:
    if prepared.status != PreparedReportStatus.NEEDS_RECIPIENTS:
        return _minimal_result(prepared, reused=False)
    if not prepared.unresolved:
        raise ValueError("MCP informou destinatários pendentes sem detalhar a resolução")

    existing = await repository.get_pending_recipient_resolution(
        principal_user_id=principal_user_id,
        session_id=session_id,
        interaction_id=interaction_id,
    )
    if existing is not None and existing.report_artifact_ref == report_artifact_ref:
        question_id = existing.question_id
    else:
        resolution = next(
            (
                item
                for item in prepared.unresolved
                if item.status == RecipientResolutionStatus.AMBIGUOUS
            ),
            prepared.unresolved[0],
        )
        options = tuple(
            PendingRecipientOption(
                option_id=str(uuid4()),
                label=f"{match.name} ({_masked_email(match.email)})",
                selector=RecipientSelector(kind="email", value=match.email),
            )
            for match in resolution.matches
        )
        ambiguous = resolution.status == RecipientResolutionStatus.AMBIGUOUS
        question_id = uuid4()
        pending = PendingRecipientResolution(
            question_id=question_id,
            principal_user_id=principal_user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            report_artifact_ref=report_artifact_ref,
            input_type="single_choice" if ambiguous else "free_text",
            reason_code="RECIPIENT_AMBIGUOUS" if ambiguous else "RECIPIENT_NOT_FOUND",
            question_goal=(
                "Escolha o destinatário correto para o relatório."
                if ambiguous
                else "Informe outro nome ou e-mail cadastrado para o destinatário."
            ),
            unresolved_selector=resolution.selector,
            options=options,
            expires_at=min(
                prepared.expires_at,
                now + timedelta(seconds=CLARIFICATION_TTL_SECONDS),
            ),
        )
        await repository.save_pending_recipient_resolution(pending)
    return json.dumps(
        {
            "status": "needs_recipients",
            "report_id": str(prepared.report_id),
            "recipient_question_id": str(question_id),
            "message": "Destinatários precisam ser resolvidos; nenhum envio foi feito.",
        },
        ensure_ascii=False,
    )


class EmailWorkflow:
    """Busca contexto confiável, prepara no MCP e persiste a resposta integral."""

    def __init__(
        self,
        *,
        email_client: EmailToolClient,
        report_artifacts: ReportArtifactRepository,
        preparations: EmailPreparationRepository,
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        if email_client.service_name != "email":
            raise ValueError("EmailWorkflow exige um cliente MCP de e-mail")
        self._email_client = email_client
        self._report_artifacts = report_artifacts
        self._preparations = preparations
        self._observation_collector = observation_collector

    async def prepare(
        self,
        *,
        report_artifact_ref: UUID,
        report_title: str,
        configurable: dict[str, Any],
    ) -> str:
        if self._observation_collector is None:
            return await self._prepare(
                report_artifact_ref=report_artifact_ref,
                report_title=report_title,
                configurable=configurable,
            )
        async with observe_operation(
            self._observation_collector,
            operation_kind=OperationKind.WORKFLOW,
            operation_name="email.prepare",
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.LOCAL_OPERATION,
            error_origin=ErrorOrigin.APPLICATION,
        ) as recorder:
            result = await self._prepare(
                report_artifact_ref=report_artifact_ref,
                report_title=report_title,
                configurable=configurable,
            )
            try:
                payload = json.loads(result)
            except TypeError, json.JSONDecodeError:
                payload = None
            code = payload.get("status") if isinstance(payload, dict) else None
            if not isinstance(code, str) or len(code) > 120:
                code = None
            recorder.finish(
                result=(ResultMetadata(source="application", code=code) if code else None)
            )
            return result

    async def _prepare(
        self,
        *,
        report_artifact_ref: UUID,
        report_title: str,
        configurable: dict[str, Any],
    ) -> str:
        try:
            principal_user_id = UUID(str(configurable["argos_user_id"]))
            session_id = UUID(str(configurable["session_id"]))
            interaction_id = UUID(str(configurable["interaction_id"]))
            property_ids = tuple(
                UUID(str(value)) for value in configurable["argos_authorized_property_ids"]
            )
            scopes = configurable["argos_report_artifact_scopes"]
            recipients_context = configurable["recipient_selectors"]
            send_intent_requested = configurable.get("argos_send_intent_requested") is True
            send_intent_source = configurable.get("argos_send_intent_source_interaction_id")
            send_origin_interaction_id = (
                UUID(str(send_intent_source)) if send_intent_source else None
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("contexto confiável para preparação está ausente") from exc

        scope = scopes.get(str(report_artifact_ref)) if isinstance(scopes, dict) else None
        if not isinstance(scope, dict):
            raise ValueError("referência de relatório não pertence às dependências autorizadas")
        report_source_interaction_id = UUID(str(scope["interaction_id"]))
        source_task_id = str(scope["task_id"])
        artifact = await self._report_artifacts.get(
            report_artifact_ref,
            principal_user_id=principal_user_id,
            session_id=session_id,
            interaction_id=report_source_interaction_id,
            task_id=source_task_id,
        )
        envelope = validate_report_source(
            artifact.envelope,
            principal_user_id=principal_user_id,
            lot_id=artifact.envelope.report.requested_lot.lot_id,
            authorized_property_ids=property_ids,
        )

        if not isinstance(report_title, str) or not report_title.strip():
            raise ValueError("título do relatório é obrigatório")
        title = report_title.strip()
        if len(title) > 200 or "\r" in title or "\n" in title:
            raise ValueError("título deve ter até 200 caracteres em uma linha")

        if not isinstance(recipients_context, dict):
            raise ValueError("seletores de destinatários protegidos estão inválidos")
        recipients = tuple(
            RecipientSelector.model_validate(selector) for selector in recipients_context.values()
        )
        if not recipients:
            return json.dumps(
                {
                    "status": "needs_recipients",
                    "message": (
                        "Informe ao menos um destinatário cadastrado para preparar o relatório."
                    ),
                },
                ensure_ascii=False,
            )

        request = PrepareReportRequest(
            report_source=envelope,
            recipients=recipients,
            report_title=title,
            requested_by=principal_user_id,
        )
        fingerprint = sha256(request.model_dump_json().encode("utf-8")).hexdigest()
        existing = await self._preparations.find_matching(
            principal_user_id=principal_user_id,
            session_id=session_id,
            report_artifact_ref=report_artifact_ref,
            request_fingerprint=fingerprint,
        )
        now = datetime.now(UTC)
        if existing is not None and now < existing.prepared_report.expires_at:
            if existing.prepared_report.status == PreparedReportStatus.NEEDS_RECIPIENTS:
                return await _recipient_question_result(
                    prepared=existing.prepared_report,
                    principal_user_id=principal_user_id,
                    session_id=session_id,
                    interaction_id=interaction_id,
                    report_artifact_ref=report_artifact_ref,
                    repository=self._preparations,
                    now=now,
                )
            await self._authorize_explicit_send_if_requested(
                preparation=existing,
                principal_user_id=principal_user_id,
                session_id=session_id,
                source_interaction_id=send_origin_interaction_id,
                requested=send_intent_requested,
                now=now,
            )
            return _minimal_result(existing.prepared_report, reused=True)

        response = await self._email_client.call_tool(
            "prepare_report",
            {"payload": request.model_dump(mode="json")},
        )
        prepared = PreparedReport.model_validate(_payload(response))
        if prepared.requested_by != principal_user_id:
            raise ValueError("MCP retornou preparação de outro solicitante")
        record = EmailPreparation(
            reference=uuid4(),
            principal_user_id=principal_user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            report_artifact_ref=report_artifact_ref,
            request_fingerprint=fingerprint,
            prepared_report=prepared,
            created_at=now,
        )
        await self._preparations.save(record)
        await self._authorize_explicit_send_if_requested(
            preparation=record,
            principal_user_id=principal_user_id,
            session_id=session_id,
            source_interaction_id=send_origin_interaction_id,
            requested=send_intent_requested,
            now=now,
        )
        return await _recipient_question_result(
            prepared=prepared,
            principal_user_id=principal_user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            report_artifact_ref=report_artifact_ref,
            repository=self._preparations,
            now=now,
        )

    async def _authorize_explicit_send_if_requested(
        self,
        *,
        preparation: EmailPreparation,
        principal_user_id: UUID,
        session_id: UUID,
        source_interaction_id: UUID | None,
        requested: bool,
        now: datetime,
    ) -> None:
        if not requested:
            return
        prepared = preparation.prepared_report
        if prepared.status != PreparedReportStatus.PREPARED or now >= prepared.expires_at:
            return
        if source_interaction_id is None:
            raise ValueError("origem do pedido explícito de envio está ausente")
        if prepared.requested_by != principal_user_id:
            raise ValueError("a preparação não pertence ao solicitante autenticado")
        prepared_payload = prepared.model_dump(mode="json")
        authorization = EmailSendAuthorization(
            authorization_id=uuid4(),
            principal_user_id=principal_user_id,
            session_id=session_id,
            source_interaction_id=source_interaction_id,
            preparation_reference=preparation.reference,
            report_id=prepared.report_id,
            request_fingerprint=preparation.request_fingerprint,
            prepared_fingerprint=_fingerprint(prepared_payload),
            recipients_fingerprint=_fingerprint(
                [recipient.model_dump(mode="json") for recipient in prepared.recipients]
            ),
            origin=SendAuthorizationOrigin.EXPLICIT_REQUEST,
            idempotency_key=str(uuid4()),
            authorized_at=now,
            expires_at=prepared.expires_at,
        )
        try:
            await self._preparations.reserve_send_authorization(authorization)
        except EmailSendAuthorizationConflictError as exc:
            raise ValueError(
                "o pedido de envio já está vinculado a outra preparação; solicite novamente"
            ) from exc
