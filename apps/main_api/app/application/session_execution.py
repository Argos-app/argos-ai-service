"""Execução e finalização das interações de sessão."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.agent_schemas.inputs import QuestionFormattingRequest
from app.application.context_builder import build_session_context
from app.application.contracts.interactions import (
    AnswerCommand,
    ConfirmationAnswer,
    InteractionOutcome,
    MessageCommand,
)
from app.application.email_helpers import _recipient_selectors_match
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.execution_limits import ExecutionLimits, execution_limits_scope
from app.application.operation_observation import OperationRecorder
from app.application.session_helpers import (
    _assistant_message,
    _memory_input,
    _mongo_utcnow,
    _response_from_state,
    _utcnow,
)
from app.application.session_service_types import (
    EMAIL_SEND_CONFIRMATION_REASON,
    _AcceptedInteraction,
    _InteractionContext,
)
from app.application.session_summary_service import SessionSummaryService
from app.application.user_memory_service import UserMemoryService
from app.domain.sessions import (
    InteractionStatus,
)
from app.ports.conversation_repository import ConversationRepository
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.session_summary_repository import SessionSummaryRepository
from app.ports.session_work_repository import SessionWorkRepository
from app.ports.user_memory_repository import UserMemoryRepository
from app.session_config import (
    CLARIFICATION_TTL_SECONDS,
)
from packages.contracts import PreparedReportStatus
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    bind_interaction,
    correlation_key,
    current_correlation_id,
)

logger = logging.getLogger("app.application.session_service")


class SessionExecution:
    def __init__(
        self,
        *,
        background_task_scheduler: Callable[[Coroutine[Any, Any, Any]], None] | None,
        conversations: ConversationRepository,
        email_preparations: EmailPreparationRepository | None,
        execution_timeout_seconds: float,
        interactions: InteractionRepository,
        langsmith_callback: Any | None,
        observation_collector: ObservationCollector | None,
        session_work: SessionWorkRepository,
        summaries: SessionSummaryRepository | None,
        summary_service: SessionSummaryService | None,
        user_memory: UserMemoryRepository | None,
        user_memory_service: UserMemoryService | None,
    ) -> None:
        self._background_task_scheduler = background_task_scheduler
        self._conversations = conversations
        self._email_preparations = email_preparations
        self._execution_timeout_seconds = execution_timeout_seconds
        self._interactions = interactions
        self._langsmith_callback = langsmith_callback
        self._observation_collector = observation_collector
        self._session_work = session_work
        self._summaries = summaries
        self._summary_service = summary_service
        self._user_memory = user_memory
        self._user_memory_service = user_memory_service

    async def _build_interaction_context(
        self, accepted: _AcceptedInteraction
    ) -> _InteractionContext:
        principal = accepted.principal
        session_id = accepted.session.session_id
        interaction_id = accepted.running.interaction_id
        memory_input = _memory_input(
            accepted.request.body, accepted.request.sanitized_input, accepted.request.answer_text
        )
        if memory_input is not None:
            with bind_interaction(session_id, interaction_id):
                await self._update_user_memory(
                    user_id=principal.user_id,
                    session_id=session_id,
                    interaction_id=interaction_id,
                    source_created_at=accepted.accepted_message.created_at,
                    sanitized_user_input=memory_input,
                )
        session_context = await build_session_context(
            session_id=session_id,
            user_id=principal.user_id,
            before_sequence=accepted.accepted_message.sequence,
            conversations=self._conversations,
            interactions=self._interactions,
            summaries=self._summaries,
            user_memory=self._user_memory,
        )
        correlation_id = current_correlation_id() or accepted.request.public_request_id
        graph_config: dict[str, Any] = {
            "configurable": {
                "argos_user_id": str(principal.user_id),
                "argos_firebase_uid": principal.firebase_uid,
                "argos_authorized_property_ids": tuple(
                    str(farm_id) for farm_id in accepted.request.authorized_farms
                ),
                "argos_request_id": str(current_correlation_id() or interaction_id),
            }
        }
        if accepted.request.confirmed_send_question_id is not None:
            graph_config["configurable"].update(
                {
                    "argos_confirmed_send_question_id": str(
                        accepted.request.confirmed_send_question_id
                    ),
                    "argos_confirmed_send_report_id": str(
                        accepted.request.confirmed_send_report_id
                    ),
                    "argos_confirmed_send_question_expires_at": (
                        accepted.request.confirmed_send_question_expires_at.isoformat()
                        if accepted.request.confirmed_send_question_expires_at is not None
                        else None
                    ),
                }
            )
        if self._langsmith_callback is not None:
            graph_config["callbacks"] = [self._langsmith_callback]
            graph_config["metadata"] = {
                "argos_operation": "session.interaction",
                "argos_correlation_id": str(correlation_id),
                "argos_correlation_key": correlation_key(correlation_id),
                "argos_session_id": str(session_id),
                "argos_interaction_id": str(interaction_id),
            }
        return _InteractionContext(session_context=session_context, graph_config=graph_config)

    async def _execute_interaction(
        self,
        accepted: _AcceptedInteraction,
        context: _InteractionContext,
        execution_limits: ExecutionLimits,
    ) -> dict[str, Any]:
        body = accepted.request.body
        session_id = accepted.session.session_id
        interaction_id = accepted.running.interaction_id
        graph_recorder = (
            OperationRecorder(
                self._observation_collector,
                operation_kind=OperationKind.GRAPH,
                operation_name=(
                    "graph.execute" if isinstance(body, MessageCommand) else "graph.resume"
                ),
                operation_phase=OperationPhase.EXECUTION,
                observation_level=ObservationLevel.LOCAL_OPERATION,
                error_origin=ErrorOrigin.APPLICATION,
                session_id=session_id,
                interaction_id=interaction_id,
            )
            if self._observation_collector is not None
            else None
        )
        try:
            with bind_interaction(session_id, interaction_id):
                with execution_limits_scope(execution_limits):
                    async with asyncio.timeout(self._execution_timeout_seconds):
                        if (
                            accepted.request.send_confirmation
                            and isinstance(body, AnswerCommand)
                            and isinstance(body.answer, ConfirmationAnswer)
                            and not body.answer.confirm
                        ):
                            state = {
                                "final_answer": "Envio cancelado. Nenhum e-mail foi enviado.",
                                "errors": [],
                            }
                            await accepted.graph.update_state(
                                str(session_id),
                                context.graph_config,
                                {
                                    "question_id": None,
                                    "clarification_request": None,
                                    "clarification_question": None,
                                    "clarification_expires_at": None,
                                    "email_send_confirmation_report_id": None,
                                    "send_intent_requested": False,
                                    "send_intent_source_interaction_id": None,
                                },
                                unavailable_message=(
                                    "o grafo não permite encerrar a confirmação pendente"
                                ),
                            )
                        elif isinstance(body, MessageCommand):
                            state = await accepted.graph.execute(
                                accepted.request.sanitized_input,
                                user_id=str(accepted.principal.user_id),
                                farm_id=str(accepted.request.authorized_farms[0]),
                                session_id=str(session_id),
                                interaction_id=str(interaction_id),
                                session_context=context.session_context,
                                recipient_selectors=accepted.request.recipient_selectors,
                                send_intent_requested=accepted.request.send_intent_requested,
                                config=context.graph_config,
                            )
                        else:
                            state = await accepted.graph.resume(
                                accepted.request.answer_text or accepted.request.sanitized_input,
                                question_id=str(body.question_id),
                                session_id=str(session_id),
                                interaction_id=str(interaction_id),
                                session_context=context.session_context,
                                recipient_selectors=accepted.request.recipient_selectors,
                                send_intent_requested=accepted.request.send_intent_requested,
                                send_intent_source_interaction_id=(
                                    accepted.request.send_intent_source_interaction_id
                                ),
                                config=context.graph_config,
                            )
        except BaseException as exc:
            if graph_recorder is not None:
                graph_recorder.finish(error=exc, error_origin=ErrorOrigin.APPLICATION)
            raise
        if graph_recorder is not None:
            graph_recorder.finish()
        state = await self._attach_send_confirmation(accepted, context, state)
        return state

    async def _attach_send_confirmation(
        self,
        accepted: _AcceptedInteraction,
        context: _InteractionContext,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """A expiração é persistida no MongoDB e no payload da interação.
        Mantém os dois horários iguais após o truncamento de milissegundos do BSON."""
        if (
            not accepted.request.send_intent_requested
            or accepted.request.confirmed_send_question_id is not None
            or accepted.request.send_confirmation
            or self._email_preparations is None
            or state.get("clarification_request") is not None
            or state.get("email_dispatch_evidence") is not None
        ):
            return state

        preparations = await self._email_preparations.list_prepared_for_session(
            principal_user_id=accepted.principal.user_id,
            session_id=accepted.session.session_id,
            limit=100,
        )
        now = _utcnow()
        candidates = tuple(
            preparation
            for preparation in preparations
            if preparation.prepared_report.requested_by == accepted.principal.user_id
            and preparation.prepared_report.status == PreparedReportStatus.PREPARED
            and now < preparation.prepared_report.expires_at
        )
        if accepted.request.recipient_selectors:
            candidates = tuple(
                preparation
                for preparation in candidates
                if _recipient_selectors_match(
                    accepted.request.recipient_selectors,
                    preparation.prepared_report.recipients,
                )
            )
        if len(candidates) != 1:
            return state

        preparation = candidates[0]
        question_expires_at = min(
            preparation.prepared_report.expires_at,
            now + timedelta(seconds=CLARIFICATION_TTL_SECONDS),
        )

        question_expires_at = question_expires_at.replace(
            microsecond=(question_expires_at.microsecond // 1000) * 1000
        )
        if question_expires_at <= now:
            return state
        recipients = ", ".join(
            f"{recipient.name} <{recipient.email}>"
            for recipient in preparation.prepared_report.recipients
        )
        subject = preparation.prepared_report.subject or "relatório preparado"
        prompt = f"Confirma o envio de ‘{subject}’ para {recipients}?"
        question_id = uuid4()
        request = QuestionFormattingRequest(
            reason_code=EMAIL_SEND_CONFIRMATION_REASON,
            input_type="confirmation",
            question_goal=prompt,
        )
        update = {
            "clarification_request": request,
            "clarification_question": prompt,
            "question_id": str(question_id),
            "clarification_expires_at": question_expires_at.isoformat(),
            "email_send_confirmation_report_id": str(preparation.prepared_report.report_id),
        }
        task = next(
            (
                item
                for item in state.get("ordered_tasks", ())
                if getattr(getattr(item, "agent", None), "value", None) == "secretary"
            ),
            None,
        )
        if task is not None:
            update["pending_task_id"] = task.task_id

        await accepted.graph.update_state(
            str(accepted.session.session_id),
            context.graph_config,
            update,
            unavailable_message="o grafo não permite persistir a confirmação pendente",
        )
        return {**state, **update}

    async def _finalize_interaction(
        self,
        accepted: _AcceptedInteraction,
        state: dict[str, Any],
        *,
        failed: bool,
    ) -> InteractionOutcome:
        if accepted.lease_lost.is_set():
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "A posse da sessão expirou durante o processamento",
            )

        session_id = accepted.session.session_id
        interaction_id = accepted.running.interaction_id
        response = _response_from_state(
            session_id=session_id,
            interaction_id=interaction_id,
            public_request_id=accepted.request.public_request_id,
            state=state,
            now=_utcnow(),
        )
        finished_at = _mongo_utcnow()
        completed = replace(
            accepted.running,
            status=(
                InteractionStatus.FAILED
                if failed
                else InteractionStatus.DEGRADED
                if response.status == "degraded"
                else InteractionStatus.COMPLETED
            ),
            result_json=response.model_dump_json(),
            updated_at=finished_at,
            version=accepted.running.version + 1,
        )
        assistant_message = _assistant_message(
            session_id=session_id,
            interaction_id=interaction_id,
            response=response,
            created_at=finished_at,
        )
        committed_message = await self._session_work.finalize_interaction(
            completed,
            assistant_message,
            accepted.lease,
            expected_version=accepted.running.version,
            now=finished_at,
        )
        if committed_message is None:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "A posse da sessão mudou antes da finalização"
            )
        summary_task = self._publish_due_summary(
            session_id=session_id,
            user_id=accepted.principal.user_id,
            interaction_id=interaction_id,
            user_message_count=accepted.accepted_message.user_message_number or 0,
            through_sequence=committed_message.sequence,
        )
        if self._background_task_scheduler is None:
            await summary_task
        else:
            self._background_task_scheduler(summary_task)
        return response.model_copy(update={"created_at": finished_at})

    async def _publish_due_summary(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        interaction_id: UUID,
        user_message_count: int,
        through_sequence: int,
    ) -> None:
        if self._summary_service is None:
            return
        try:
            with bind_interaction(session_id, interaction_id):
                await self._summary_service.publish_due(
                    session_id=session_id,
                    user_id=user_id,
                    user_message_count=user_message_count,
                    through_sequence=through_sequence,
                )
        except Exception:
            logger.exception(
                "falha ao resumir sessão %s; resumo anterior e cobertura foram mantidos",
                session_id,
            )

    async def _update_user_memory(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        interaction_id: UUID,
        source_created_at: datetime,
        sanitized_user_input: str,
    ) -> None:
        if self._user_memory_service is None:
            return
        try:
            await self._user_memory_service.update_from_user_entry(
                user_id=user_id,
                session_id=session_id,
                interaction_id=interaction_id,
                source_created_at=source_created_at,
                sanitized_user_input=sanitized_user_input,
            )
        except Exception:
            logger.exception(
                "falha ao atualizar memória do usuário %s; interação seguirá com memória atual",
                user_id,
            )
