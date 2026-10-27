"""Replay, validação de respostas e renovação do lease de sessão."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from app.application.contracts.interactions import (
    AnswerCommand,
    ChoiceAnswer,
    ConfirmationAnswer,
    InteractionOutcome,
    QuestionResult,
    TextAnswer,
)
from app.application.email_helpers import _recipient_selectors_match
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.operation_observation import OperationRecorder
from app.application.session_helpers import (
    _utcnow,
)
from app.application.session_service_types import (
    EMAIL_SEND_CONFIRMATION_REASON,
)
from app.domain.sessions import (
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    ProcessingLease,
    SessionRecord,
)
from app.ports.conversation_flow import ConversationFlow
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.session_repository import SessionRepository
from app.privacy import merge_recipient_selectors, protect_user_input
from packages.contracts import PreparedReportStatus, RecipientSelector
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)

logger = logging.getLogger("app.application.session_service")


class SessionRecovery:
    def __init__(
        self,
        *,
        email_preparations: EmailPreparationRepository | None,
        interactions: InteractionRepository,
        lease_seconds: int,
        observation_collector: ObservationCollector | None,
        renew_seconds: float,
        sessions: SessionRepository,
    ) -> None:
        self._email_preparations = email_preparations
        self._interactions = interactions
        self._lease_seconds = lease_seconds
        self._observation_collector = observation_collector
        self._renew_seconds = renew_seconds
        self._sessions = sessions

    async def _find_existing(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        kind: InteractionKind,
        deduplication_key: str,
        input_hash: str,
    ) -> InteractionRecord | None:
        existing = await self._interactions.find_by_deduplication_key(
            session_id,
            user_id,
            kind=kind,
            deduplication_key=deduplication_key,
        )
        if existing is not None and existing.input_hash != input_hash:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "A chave da requisição já foi usada com outro conteúdo",
            )
        return existing

    async def _replay_or_reject(
        self, interaction: InteractionRecord, session: SessionRecord
    ) -> InteractionOutcome:
        recorder = (
            OperationRecorder(
                self._observation_collector,
                operation_kind=OperationKind.INTERACTION,
                operation_name="interaction.replay_or_recovery",
                operation_phase=OperationPhase.RECOVERY,
                observation_level=ObservationLevel.LOCAL_OPERATION,
                error_origin=ErrorOrigin.APPLICATION,
                session_id=interaction.session_id,
                interaction_id=interaction.interaction_id,
            )
            if self._observation_collector is not None
            else None
        )
        try:
            result = await self._resolve_existing_interaction(interaction, session)
        except BaseException as exc:
            if recorder is not None:
                recorder.finish(error=exc)
            raise
        if recorder is not None:
            is_replay = interaction.status in {
                InteractionStatus.COMPLETED,
                InteractionStatus.DEGRADED,
                InteractionStatus.FAILED,
            }
            recorder.finish(
                result=ResultMetadata(
                    source="application",
                    code="replayed_result" if is_replay else "recovery_result",
                    recovered=not is_replay,
                ),
                replayed_result=is_replay,
            )
        return result

    async def _resolve_existing_interaction(
        self, interaction: InteractionRecord, session: SessionRecord
    ) -> InteractionOutcome:
        if interaction.status in {
            InteractionStatus.COMPLETED,
            InteractionStatus.DEGRADED,
            InteractionStatus.FAILED,
        }:
            if interaction.result_json:
                result = InteractionOutcome.model_validate_json(interaction.result_json)
                if result.created_at is None:
                    result = result.model_copy(update={"created_at": interaction.updated_at})
                return result
        now = _utcnow()
        lease = session.processing_lease
        if interaction.status in {InteractionStatus.ACCEPTED, InteractionStatus.RUNNING} and (
            lease is None or lease.expires_at <= now
        ):
            interrupted = replace(
                interaction,
                status=InteractionStatus.INTERRUPTED,
                updated_at=now,
                version=interaction.version + 1,
            )
            await self._interactions.save_result(interrupted, expected_version=interaction.version)
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "A interação anterior foi interrompida e não será reexecutada",
            )
        raise ApplicationError(
            ApplicationErrorCode.CONFLICT, "A interação está em andamento ou foi interrompida"
        )

    async def _validate_answer(
        self,
        session: SessionRecord,
        body: AnswerCommand,
        graph: ConversationFlow,
        *,
        new_recipient_selectors: dict[str, dict[str, str]],
    ) -> tuple[
        str,
        dict[str, dict[str, str]],
        bool,
        str | None,
        bool,
        UUID | None,
        UUID | None,
        datetime | None,
    ]:
        source = await self._interactions.get_owned(body.interaction_id, session.user_id)
        if (
            source is None
            or source.session_id != session.session_id
            or source.status not in {InteractionStatus.COMPLETED, InteractionStatus.DEGRADED}
            or not source.result_json
        ):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Pergunta inválida")
        try:
            source_result = InteractionOutcome.model_validate_json(source.result_json)
        except ValueError as exc:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Pergunta inválida") from exc
        question = source_result.response
        now = _utcnow()
        if (
            not isinstance(question, QuestionResult)
            or question.question_id != body.question_id
            or question.expires_at <= now
        ):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "A pergunta não está mais pendente"
            )
        pending_recipient_resolution = None
        if question.reason_code in {"RECIPIENT_AMBIGUOUS", "RECIPIENT_NOT_FOUND"}:
            if self._email_preparations is None:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "A resolução de destinatário não está disponível"
                )
            try:
                pending_recipient_resolution = (
                    await self._email_preparations.get_pending_recipient_resolution(
                        principal_user_id=session.user_id,
                        session_id=session.session_id,
                        interaction_id=body.interaction_id,
                        question_id=body.question_id,
                    )
                )
            except Exception as exc:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE,
                    "A resolução de destinatário está indisponível",
                ) from exc
            if (
                pending_recipient_resolution is None
                or pending_recipient_resolution.reason_code != question.reason_code
                or pending_recipient_resolution.input_type != question.input_type
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "A resolução de destinatário expirou ou não corresponde à pergunta",
                )
        recipient_update: dict[str, dict[str, str]] = new_recipient_selectors
        if isinstance(body.answer, TextAnswer):
            if question.input_type != "free_text":
                raise ApplicationError(
                    ApplicationErrorCode.INVALID_INPUT, "Tipo de resposta incompatível"
                )
            if pending_recipient_resolution is not None:
                value = body.answer.text.strip()
                try:
                    selector = RecipientSelector(
                        kind="email" if "@" in value else "name",
                        value=value,
                    )
                except ValueError as exc:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID_INPUT, "Destinatário inválido"
                    ) from exc
                graph_answer = "[DESTINATARIO_1]"
                recipient_update = {"[DESTINATARIO_1]": selector.model_dump(mode="json")}
            else:
                protected_answer = protect_user_input(body.answer.text)
                graph_answer = protected_answer.sanitized_text
        elif isinstance(body.answer, ChoiceAnswer):
            if question.input_type != "single_choice":
                raise ApplicationError(
                    ApplicationErrorCode.INVALID_INPUT, "Tipo de resposta incompatível"
                )
            option = next(
                (item for item in question.options if item.option_id == body.answer.option_id), None
            )
            if option is None:
                raise ApplicationError(ApplicationErrorCode.INVALID_INPUT, "Opção inválida")
            if pending_recipient_resolution is not None:
                selected = next(
                    (
                        item
                        for item in pending_recipient_resolution.options
                        if item.option_id == body.answer.option_id
                    ),
                    None,
                )
                if selected is None:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID_INPUT, "Opção de destinatário inválida"
                    )
                graph_answer = "[DESTINATARIO_1]"
                recipient_update = {"[DESTINATARIO_1]": selected.selector.model_dump(mode="json")}
            else:
                graph_answer = option.label
        elif isinstance(body.answer, ConfirmationAnswer):
            if question.input_type != "confirmation":
                raise ApplicationError(
                    ApplicationErrorCode.INVALID_INPUT, "Tipo de resposta incompatível"
                )
            graph_answer = "Sim" if body.answer.confirm else "Não"
        else:
            raise ApplicationError(ApplicationErrorCode.INVALID_INPUT, "Resposta inválida")

        try:
            values = await graph.read_state(str(session.session_id))
        except Exception as exc:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "O estado da pergunta não está disponível para retomada",
            ) from exc
        if values.get("question_id") != str(body.question_id):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "A pergunta não corresponde ao estado atual da sessão",
            )
        send_confirmation = question.reason_code == EMAIL_SEND_CONFIRMATION_REASON
        confirmed_send_report_id: UUID | None = None
        confirmed_send_question_expires_at: datetime | None = None
        if (
            send_confirmation
            and isinstance(body.answer, ConfirmationAnswer)
            and body.answer.confirm
        ):
            if not values.get("send_intent_requested"):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "A solicitação de envio não está mais autorizada",
                )
            try:
                confirmed_send_report_id = UUID(str(values["email_send_confirmation_report_id"]))
                confirmed_send_question_expires_at = question.expires_at
            except (KeyError, TypeError, ValueError) as exc:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "A confirmação não está vinculada a um relatório",
                ) from exc
            if self._email_preparations is None:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE,
                    "A autorização de envio está indisponível",
                )
            try:
                preparations = await self._email_preparations.list_prepared_for_session(
                    principal_user_id=session.user_id,
                    session_id=session.session_id,
                    limit=100,
                )
            except Exception as exc:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE,
                    "Não foi possível validar o relatório preparado",
                ) from exc
            valid_preparations = tuple(
                preparation
                for preparation in preparations
                if preparation.prepared_report.report_id == confirmed_send_report_id
                and preparation.prepared_report.requested_by == session.user_id
                and preparation.prepared_report.status == PreparedReportStatus.PREPARED
                and now < preparation.prepared_report.expires_at
            )
            if len(valid_preparations) != 1:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "O relatório preparado expirou ou não está mais disponível",
                )
            saved_selectors = values.get("recipient_selectors") or {}
            if saved_selectors:
                if not _recipient_selectors_match(
                    saved_selectors, valid_preparations[0].prepared_report.recipients
                ):
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "Os destinatários não correspondem ao relatório preparado",
                    )
        saved_recipient_selectors = dict(values.get("recipient_selectors") or {})
        if pending_recipient_resolution is not None:
            unresolved = pending_recipient_resolution.unresolved_selector
            saved_recipient_selectors = {
                reference: selector
                for reference, selector in saved_recipient_selectors.items()
                if not (
                    selector.get("kind") == unresolved.kind
                    and selector.get("value", "").strip().casefold()
                    == unresolved.value.strip().casefold()
                )
            }
        recipient_selectors, graph_answer = merge_recipient_selectors(
            saved_recipient_selectors,
            recipient_update,
            graph_answer,
        )
        send_intent_requested = bool(values.get("send_intent_requested"))
        source_interaction_id = values.get("send_intent_source_interaction_id")
        if send_intent_requested and source_interaction_id is None:
            source_interaction_id = str(body.interaction_id)
        return (
            graph_answer,
            recipient_selectors,
            send_intent_requested,
            source_interaction_id,
            send_confirmation,
            body.question_id
            if send_confirmation
            and isinstance(body.answer, ConfirmationAnswer)
            and body.answer.confirm
            else None,
            confirmed_send_report_id,
            confirmed_send_question_expires_at,
        )

    async def _renew_lease(
        self,
        session_id: UUID,
        lease: ProcessingLease,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._renew_seconds)
                return
            except TimeoutError:
                now = _utcnow()
                try:
                    renewed = await self._sessions.renew_lease(
                        session_id,
                        lease,
                        now=now,
                        expires_at=now + timedelta(seconds=self._lease_seconds),
                    )
                except Exception as exc:
                    logger.warning(
                        "session lease renewal failed",
                        extra={"session_id": str(session_id), "error_type": type(exc).__name__},
                    )
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return
