"""Casos de uso de sessões e processamento síncrono de interações."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Collection, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.agent_schemas.inputs import QuestionFormattingRequest, SessionContext
from app.application.context_builder import build_session_context
from app.application.contracts.interactions import (
    AnswerCommand,
    AnswerResult,
    ChoiceAnswer,
    ConfirmationAnswer,
    InteractionCommand,
    InteractionOperation,
    InteractionOutcome,
    MessageCommand,
    QuestionOptionResult,
    QuestionResult,
    TextAnswer,
)
from app.application.contracts.sessions import SessionView
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.execution_limits import ExecutionLimits, execution_limits_scope
from app.application.identity import ActorContext
from app.application.operation_observation import OperationRecorder, observe_operation
from app.application.session_summary_service import SessionSummaryService
from app.application.user_memory_service import UserMemoryService
from app.application.verified_evidence import verified_evidence_fallback
from app.domain.sessions import (
    ConversationMessage,
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    MessageKind,
    MessageRole,
    ProcessingLease,
    SessionRecord,
)
from app.ports.conversation_repository import ConversationRepository
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.execution_event_repository import ExecutionEventRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.memory_generators import SessionSummaryGenerator, UserMemoryExtractor
from app.ports.session_repository import SessionRepository
from app.ports.session_summary_repository import SessionSummaryRepository
from app.ports.session_work_repository import SessionWorkRepository
from app.ports.user_memory_repository import UserMemoryRepository
from app.privacy import merge_recipient_selectors, minimize_pii, protect_user_input
from app.session_config import (
    CLARIFICATION_TTL_SECONDS,
    SESSION_LEASE_RENEW_SECONDS,
    SESSION_LEASE_SECONDS,
)
from packages.contracts import PreparedReportStatus, RecipientSelector
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
    bind_interaction,
    correlation_key,
    current_correlation_id,
)

GraphProvider = Callable[[], Any]
SummaryGeneratorProvider = Callable[[], SessionSummaryGenerator | None]
MemoryExtractorProvider = Callable[[], UserMemoryExtractor | None]
BackgroundTaskScheduler = Callable[[Coroutine[Any, Any, Any]], None]
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."
logger = logging.getLogger(__name__)
EMAIL_SEND_CONFIRMATION_REASON = "EMAIL_SEND_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class _InteractionRequest:
    body: InteractionCommand
    authorized_farms: tuple[UUID, ...]
    sanitized_input: str
    public_request_id: UUID
    recipient_selectors: dict[str, dict[str, str]]
    send_intent_requested: bool
    answer_text: str | None
    send_intent_source_interaction_id: str | None
    send_confirmation: bool = False
    confirmed_send_question_id: UUID | None = None
    confirmed_send_report_id: UUID | None = None
    confirmed_send_question_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _AcceptedInteraction:
    principal: ActorContext
    session: SessionRecord
    request: _InteractionRequest
    graph: Any
    accepted_message: ConversationMessage
    lease: ProcessingLease
    lease_lost: asyncio.Event
    running: InteractionRecord


@dataclass(frozen=True, slots=True)
class _InteractionContext:
    session_context: SessionContext
    graph_config: dict[str, Any]


class SessionApplicationService:
    """Coordena sessões, interações e persistência pelas portas injetadas."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        interactions: InteractionRepository,
        conversations: ConversationRepository,
        session_work: SessionWorkRepository,
        graph_provider: GraphProvider,
        summaries: SessionSummaryRepository | None = None,
        user_memory: UserMemoryRepository | None = None,
        execution_events: ExecutionEventRepository | None = None,
        observation_collector: ObservationCollector | None = None,
        langsmith_callback: Any | None = None,
        email_preparations: EmailPreparationRepository | None = None,
        summary_generator_provider: SummaryGeneratorProvider | None = None,
        user_memory_extractor_provider: MemoryExtractorProvider | None = None,
        background_task_scheduler: BackgroundTaskScheduler | None = None,
        lease_seconds: int = SESSION_LEASE_SECONDS,
        renew_seconds: int = SESSION_LEASE_RENEW_SECONDS,
        execution_max_model_calls: int = 12,
        execution_max_tool_calls: int = 12,
        execution_timeout_seconds: float = 90.0,
    ) -> None:
        self._sessions = sessions
        self._interactions = interactions
        self._conversations = conversations
        self._session_work = session_work
        self._graph_provider = graph_provider
        self._execution_max_model_calls = execution_max_model_calls
        self._execution_max_tool_calls = execution_max_tool_calls
        self._execution_timeout_seconds = execution_timeout_seconds
        self._background_task_scheduler = background_task_scheduler
        self._observation_collector = observation_collector
        self._langsmith_callback = langsmith_callback
        self._email_preparations = email_preparations
        self._summaries = summaries
        self._user_memory = user_memory
        self._user_memory_service = (
            UserMemoryService(
                repository=user_memory,
                extractor_provider=user_memory_extractor_provider,
            )
            if user_memory is not None
            else None
        )
        self._summary_service = (
            SessionSummaryService(
                summaries=summaries,
                conversations=conversations,
                interactions=interactions,
                execution_events=execution_events,
                generator_provider=summary_generator_provider,
            )
            if summaries is not None
            and execution_events is not None
            and summary_generator_provider is not None
            else None
        )
        self._lease_seconds = lease_seconds
        self._renew_seconds = renew_seconds

    async def create_session(self, principal: ActorContext) -> SessionView:
        if not principal.farm_ids:
            raise ApplicationError(
                ApplicationErrorCode.FORBIDDEN, "O usuário não possui fazendas autorizadas"
            )
        # MongoDB stores BSON datetimes at millisecond precision.
        now = _mongo_utcnow()
        record = SessionRecord(
            session_id=uuid4(),
            user_id=principal.user_id,
            farm_ids=tuple(dict.fromkeys(principal.farm_ids)),
            created_at=now,
            updated_at=now,
        )
        await self._sessions.create(record)
        response = _session_response(record, principal.farm_ids)
        assert response is not None
        return response

    async def list_sessions(self, principal: ActorContext) -> list[SessionView]:
        return [
            response
            for record in await self._sessions.list_owned(principal.user_id)
            if (response := _session_response(record, principal.farm_ids)) is not None
        ]

    async def get_interaction_operation(
        self, principal: ActorContext, session_id: UUID, operation_id: UUID
    ) -> InteractionOperation:
        session = await self._sessions.get_owned(session_id, principal.user_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Sessão não encontrada")
        if not _authorized_farm_ids(session, principal.farm_ids):
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Sessão não encontrada")
        if not _has_full_farm_access(session, principal.farm_ids):
            raise ApplicationError(
                ApplicationErrorCode.FORBIDDEN,
                "A sessão perdeu acesso a uma ou mais fazendas; consulte o histórico ocultado",
            )
        interaction = await self._interactions.get_owned(operation_id, principal.user_id)
        if interaction is None or interaction.session_id != session_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Interação não encontrada")

        if interaction.status in {InteractionStatus.ACCEPTED, InteractionStatus.RUNNING}:
            lease = session.processing_lease
            if lease is None or lease.expires_at <= _utcnow():
                interrupted = replace(
                    interaction,
                    status=InteractionStatus.INTERRUPTED,
                    updated_at=_mongo_utcnow(),
                    version=interaction.version + 1,
                )
                if await self._interactions.save_result(
                    interrupted, expected_version=interaction.version
                ):
                    interaction = interrupted
                else:
                    interaction = await self._interactions.get_owned(
                        operation_id, principal.user_id
                    )
                    if interaction is None or interaction.session_id != session_id:
                        raise ApplicationError(
                            ApplicationErrorCode.NOT_FOUND, "Interação não encontrada"
                        )

        result = (
            InteractionOutcome.model_validate_json(interaction.result_json)
            if interaction.result_json
            else None
        )
        if result is not None and result.created_at is None:
            result = result.model_copy(update={"created_at": interaction.updated_at})
        return InteractionOperation(
            operation_id=operation_id,
            status=interaction.status.value,
            result=result,
        )

    async def create_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> InteractionOutcome:
        return await self._observe_interaction(
            self._create_interaction(principal, session_id, body)
        )

    async def submit_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> UUID:
        """Persiste a aceitação e agenda a execução sem prender a chamada HTTP."""
        manager: AbstractAsyncContextManager[_AcceptedInteraction | InteractionOutcome] = (
            self._accept_interaction(principal, session_id, body)
        )
        try:
            accepted = await manager.__aenter__()
        except ApplicationError as exc:
            if exc.code is not ApplicationErrorCode.CONFLICT:
                raise
            kind, deduplication_key, _, _, input_hash, *_ = _normalize_input(body)
            existing = await self._interactions.find_by_deduplication_key(
                session_id,
                principal.user_id,
                kind=kind,
                deduplication_key=deduplication_key,
            )
            if (
                existing is not None
                and existing.input_hash == input_hash
                and existing.status in {InteractionStatus.ACCEPTED, InteractionStatus.RUNNING}
            ):
                return existing.interaction_id
            raise
        if isinstance(accepted, InteractionOutcome):
            await manager.__aexit__(None, None, None)
            return accepted.interaction_id
        if self._background_task_scheduler is None:
            await manager.__aexit__(None, None, None)
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE,
                "Processamento assíncrono não está configurado",
            )
        try:
            self._background_task_scheduler(
                self._process_and_release_interaction(manager, accepted)
            )
        except BaseException as exc:
            await manager.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        return accepted.running.interaction_id

    async def _observe_interaction(
        self, interaction: Coroutine[Any, Any, InteractionOutcome]
    ) -> InteractionOutcome:
        if self._observation_collector is None:
            return await interaction
        async with observe_operation(
            self._observation_collector,
            operation_kind=OperationKind.INTERACTION,
            operation_name="interaction.process",
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.LOCAL_OPERATION,
            error_origin=ErrorOrigin.APPLICATION,
        ) as recorder:
            result = await interaction
            recorder.finish(
                result=ResultMetadata(
                    source="application",
                    code=result.response.kind,
                )
            )
            return result

    async def _process_and_release_interaction(
        self,
        manager: AbstractAsyncContextManager[_AcceptedInteraction | InteractionOutcome],
        accepted: _AcceptedInteraction,
    ) -> None:
        try:
            await self._observe_interaction(self._process_accepted(accepted))
        except Exception:
            logger.exception(
                "assíncrono não conseguiu finalizar interação %s",
                accepted.running.interaction_id,
            )
        finally:
            try:
                await manager.__aexit__(None, None, None)
            except Exception:
                logger.exception(
                    "falha ao liberar lease da interação %s",
                    accepted.running.interaction_id,
                )

    async def _create_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> InteractionOutcome:
        async with self._accept_interaction(principal, session_id, body) as accepted:
            if isinstance(accepted, InteractionOutcome):
                return accepted

            return await self._process_accepted(accepted)

    async def _process_accepted(self, accepted: _AcceptedInteraction) -> InteractionOutcome:
        if accepted.lease_lost.is_set():
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "A posse da sessão foi perdida durante o processamento",
            )
        processing = asyncio.create_task(self._process_accepted_interaction(accepted))
        lease_watch = asyncio.create_task(accepted.lease_lost.wait())
        try:
            await asyncio.wait((processing, lease_watch), return_when=asyncio.FIRST_COMPLETED)
            if accepted.lease_lost.is_set():
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "A posse da sessão foi perdida durante o processamento",
                )
            return await processing
        finally:
            processing.cancel()
            lease_watch.cancel()
            await asyncio.gather(processing, lease_watch, return_exceptions=True)

    async def _process_accepted_interaction(
        self, accepted: _AcceptedInteraction
    ) -> InteractionOutcome:
        execution_limits = ExecutionLimits(
            max_model_calls=self._execution_max_model_calls,
            max_tool_calls=self._execution_max_tool_calls,
        )
        failed = False
        try:
            context = await self._build_interaction_context(accepted)
            state = await self._execute_interaction(accepted, context, execution_limits)
        except TimeoutError as exc:
            logger.warning(
                "session interaction exceeded execution timeout",
                extra={"error_type": type(exc).__name__},
            )
            evidence = execution_limits.evidence_snapshot()
            state = {
                "final_answer": (
                    verified_evidence_fallback(evidence, unavailable_stage="processamento")
                    or "O processamento excedeu o tempo limite antes de obter "
                    "um resultado verificável. Tente novamente."
                ),
                "errors": ["execution_timeout"],
            }
            failed = True
        except Exception as exc:
            logger.error(
                "session interaction graph failed",
                extra={"error_type": type(exc).__name__},
            )
            state = {"final_answer": SAFE_ANSWER, "errors": ["graph_execution_failed"]}
            failed = True
        return await self._finalize_interaction(accepted, state, failed=failed)

    @asynccontextmanager
    async def _accept_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> AsyncIterator[_AcceptedInteraction | InteractionOutcome]:
        session = await self._sessions.get_owned(session_id, principal.user_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Sessão não encontrada")
        authorized_farms = _authorized_farm_ids(session, principal.farm_ids)
        if not _has_full_farm_access(session, principal.farm_ids):
            raise ApplicationError(
                ApplicationErrorCode.FORBIDDEN,
                "A sessão perdeu acesso a uma ou mais fazendas; crie uma nova sessão",
            )

        (
            kind,
            deduplication_key,
            sanitized_input,
            payload_json,
            input_hash,
            public_request_id,
            recipient_selectors,
            send_intent_requested,
        ) = _normalize_input(body)
        existing = await self._find_existing(
            session_id,
            principal.user_id,
            kind=kind,
            deduplication_key=deduplication_key,
            input_hash=input_hash,
        )
        if existing is not None:
            yield await self._replay_or_reject(existing, session)
            return

        graph = self._graph_provider()
        if graph is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Modelos de interação não configurados"
            )

        answer_text: str | None = None
        send_intent_source_interaction_id: str | None = None
        send_confirmation = False
        confirmed_send_question_id: UUID | None = None
        confirmed_send_report_id: UUID | None = None
        confirmed_send_question_expires_at: datetime | None = None
        if isinstance(body, AnswerCommand):
            (
                answer_text,
                recipient_selectors,
                send_intent_requested,
                send_intent_source_interaction_id,
                send_confirmation,
                confirmed_send_question_id,
                confirmed_send_report_id,
                confirmed_send_question_expires_at,
            ) = await self._validate_answer(
                session, body, graph, new_recipient_selectors=recipient_selectors
            )

        # MongoDB stores BSON datetimes at millisecond precision. Keep every
        # persisted interaction timestamp at that precision so a fast completion
        # cannot be rounded to a time before its creation time.
        now = _mongo_utcnow()
        lease = await self._sessions.acquire_lease(
            session_id,
            principal.user_id,
            owner_id=uuid4(),
            now=now,
            expires_at=now + timedelta(seconds=self._lease_seconds),
        )
        if lease is None:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Outra interação está processando esta sessão"
            )

        stop_renewal = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_lease(session_id, lease, stop_renewal, lease_lost)
        )
        try:
            existing = await self._find_existing(
                session_id,
                principal.user_id,
                kind=kind,
                deduplication_key=deduplication_key,
                input_hash=input_hash,
            )
            if existing is not None:
                yield await self._replay_or_reject(existing, session)
                return

            interaction_id = _interaction_id(session_id, kind, deduplication_key, input_hash)
            interaction = InteractionRecord(
                interaction_id=interaction_id,
                session_id=session_id,
                user_id=principal.user_id,
                deduplication_key=deduplication_key,
                kind=kind,
                sanitized_input=sanitized_input,
                input_hash=input_hash,
                status=InteractionStatus.ACCEPTED,
                created_at=now,
                updated_at=now,
            )
            user_message = ConversationMessage(
                message_id=uuid5(interaction_id, "user"),
                session_id=session_id,
                interaction_id=interaction_id,
                sequence=1,
                role=MessageRole.USER,
                kind=MessageKind.USER_INPUT,
                sanitized_content=sanitized_input,
                created_at=now,
                user_message_number=1,
                payload_json=payload_json,
            )
            try:
                accepted_message = await self._session_work.accept_user_interaction(
                    interaction, user_message, lease, now=now
                )
            except Exception as exc:
                if getattr(exc, "code", None) == 11000:
                    duplicate = await self._find_existing(
                        session_id,
                        principal.user_id,
                        kind=kind,
                        deduplication_key=deduplication_key,
                        input_hash=input_hash,
                    )
                    if duplicate is not None:
                        yield await self._replay_or_reject(duplicate, session)
                        return
                raise
            if accepted_message is None:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "A posse da sessão mudou antes da aceitação"
                )

            running = replace(
                interaction,
                status=InteractionStatus.RUNNING,
                updated_at=_mongo_utcnow(),
                version=interaction.version + 1,
            )
            if not await self._interactions.save_result(
                running, expected_version=interaction.version
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "A interação mudou durante o processamento"
                )
            yield _AcceptedInteraction(
                principal=principal,
                session=session,
                request=_InteractionRequest(
                    body=body,
                    authorized_farms=authorized_farms,
                    sanitized_input=sanitized_input,
                    public_request_id=public_request_id,
                    recipient_selectors=recipient_selectors,
                    send_intent_requested=send_intent_requested,
                    answer_text=answer_text,
                    send_intent_source_interaction_id=send_intent_source_interaction_id,
                    send_confirmation=send_confirmation,
                    confirmed_send_question_id=confirmed_send_question_id,
                    confirmed_send_report_id=confirmed_send_report_id,
                    confirmed_send_question_expires_at=confirmed_send_question_expires_at,
                ),
                graph=graph,
                accepted_message=accepted_message,
                lease=lease,
                lease_lost=lease_lost,
                running=running,
            )
        finally:
            await self._release_interaction_lease(session_id, lease, stop_renewal, heartbeat)

    async def _release_interaction_lease(
        self,
        session_id: UUID,
        lease: ProcessingLease,
        stop_renewal: asyncio.Event,
        heartbeat: asyncio.Task[None],
    ) -> None:
        stop_renewal.set()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await self._sessions.release_lease(session_id, lease)

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
        from app.graph.workflow import executar_fluxo, executar_resposta

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
                            updater = getattr(accepted.graph, "aupdate_state", None)
                            if not callable(updater):
                                raise RuntimeError(
                                    "o grafo não permite encerrar a confirmação pendente"
                                )
                            from app.graph.workflow import build_session_config

                            await updater(
                                build_session_config(str(session_id), context.graph_config),
                                {
                                    "question_id": None,
                                    "clarification_request": None,
                                    "clarification_question": None,
                                    "clarification_expires_at": None,
                                    "email_send_confirmation_report_id": None,
                                    "send_intent_requested": False,
                                    "send_intent_source_interaction_id": None,
                                },
                            )
                        elif isinstance(body, MessageCommand):
                            state = await executar_fluxo(
                                accepted.graph,
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
                            state = await executar_resposta(
                                accepted.graph,
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
            from app.application.email_dispatch_workflow import _recipient_selectors_match

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
        # The expiry is persisted in both MongoDB and the interaction payload.
        # Keep them equal after BSON's millisecond precision truncation.
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

        from app.graph.workflow import build_session_config

        updater = getattr(accepted.graph, "aupdate_state", None)
        if not callable(updater):
            raise RuntimeError("o grafo não permite persistir a confirmação pendente")
        await updater(
            build_session_config(str(accepted.session.session_id), context.graph_config),
            update,
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
        graph: Any,
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
            snapshot = await graph.aget_state(
                {
                    "configurable": {
                        "thread_id": str(session.session_id),
                    }
                }
            )
        except Exception as exc:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "O estado da pergunta não está disponível para retomada",
            ) from exc
        if getattr(snapshot, "values", {}).get("question_id") != str(body.question_id):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "A pergunta não corresponde ao estado atual da sessão",
            )
        values = getattr(snapshot, "values", {})
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
                from app.application.email_dispatch_workflow import _recipient_selectors_match

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


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _mongo_utcnow() -> datetime:
    """Use MongoDB's millisecond precision for timestamps returned and persisted together."""
    now = _utcnow()
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _authorized_farm_ids(
    session: SessionRecord, current_farms: Collection[UUID]
) -> tuple[UUID, ...]:
    current = set(current_farms)
    return tuple(farm_id for farm_id in session.farm_ids if farm_id in current)


def _has_full_farm_access(session: SessionRecord, current_farms: Collection[UUID]) -> bool:
    """Novas interações exigem acesso atual a todas as fazendas originais."""
    original = set(session.farm_ids)
    return bool(original) and original.issubset(set(current_farms))


def _session_response(
    session: SessionRecord, current_farms: Collection[UUID]
) -> SessionView | None:
    farm_ids = _authorized_farm_ids(session, current_farms)
    if not farm_ids:
        return None
    return SessionView(
        session_id=session.session_id,
        farm_ids=farm_ids,
        created_at=session.created_at,
    )


def _normalize_input(
    body: InteractionCommand,
) -> tuple[
    InteractionKind,
    str,
    str,
    str,
    str,
    UUID,
    dict[str, dict[str, str]],
    bool,
]:
    recipient_selectors: dict[str, dict[str, str]] = {}
    send_intent_requested = False
    if isinstance(body, MessageCommand):
        protected = protect_user_input(body.message)
        sanitized = protected.sanitized_text
        recipient_selectors = protected.recipient_selectors
        send_intent_requested = protected.send_intent_requested
        if not sanitized.strip():
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT, "A mensagem não pode estar vazia"
            )
        payload = {"kind": "message", "message": sanitized}
        key = str(body.client_message_id)
        public_id = body.client_message_id
        kind = InteractionKind.MESSAGE
        content = sanitized
    else:
        answer = body.answer.model_dump(mode="json")
        if isinstance(body.answer, TextAnswer):
            protected = protect_user_input(body.answer.text)
            answer["text"] = protected.sanitized_text
            recipient_selectors = protected.recipient_selectors
            content = answer["text"]
        elif isinstance(body.answer, ChoiceAnswer):
            content = f"Opção selecionada: {body.answer.option_id}"
        else:
            content = "Sim" if body.answer.confirm else "Não"
        if not content.strip():
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT, "A resposta não pode estar vazia"
            )
        payload = {
            "kind": "answer",
            "interaction_id": str(body.interaction_id),
            "question_id": str(body.question_id),
            "answer": answer,
        }
        key = str(body.question_id)
        public_id = body.question_id
        kind = InteractionKind.ANSWER
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    protected_digest = hashlib.sha256(
        json.dumps(recipient_selectors, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    hash_payload = f"{payload_json}:{protected_digest}:{send_intent_requested}"
    digest = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
    return (
        kind,
        key,
        content,
        payload_json,
        digest,
        public_id,
        recipient_selectors,
        send_intent_requested,
    )


def _memory_input(
    body: InteractionCommand,
    sanitized_input: str,
    answer_text: str | None,
) -> str | None:
    if isinstance(body, MessageCommand):
        return sanitized_input
    if isinstance(body, AnswerCommand) and isinstance(body.answer, TextAnswer):
        return answer_text or sanitized_input
    return None


def _interaction_id(
    session_id: UUID,
    kind: InteractionKind,
    key: str,
    input_hash: str,
) -> UUID:
    return uuid5(NAMESPACE_URL, f"argos:{session_id}:{kind.value}:{key}:{input_hash}")


def _response_from_state(
    *,
    session_id: UUID,
    interaction_id: UUID,
    public_request_id: UUID,
    state: dict[str, Any],
    now: datetime,
) -> InteractionOutcome:
    request = state.get("clarification_request")
    prompt = state.get("clarification_question")
    question_id = state.get("question_id")
    if request is not None and prompt and question_id:
        expires_at = now + timedelta(seconds=CLARIFICATION_TTL_SECONDS)
        pending_expiry = state.get("clarification_expires_at")
        if pending_expiry:
            try:
                expires_at = min(expires_at, datetime.fromisoformat(pending_expiry))
            except TypeError, ValueError:
                expires_at = now
        if expires_at <= now:
            response = AnswerResult(
                kind="answer",
                text="A pergunta expirou. Inicie uma nova solicitação para continuar.",
            )
        else:
            response = QuestionResult(
                kind="question",
                question_id=UUID(str(question_id)),
                input_type=request.input_type,
                prompt=minimize_pii(_hide_report_artifact_refs(prompt, state)),
                reason_code=request.reason_code,
                options=[
                    QuestionOptionResult.model_validate(item.model_dump())
                    for item in request.options
                ],
                expires_at=expires_at,
            )
    else:
        response = AnswerResult(
            kind="answer",
            text=minimize_pii(
                _hide_report_artifact_refs(state.get("final_answer") or SAFE_ANSWER, state)
            ),
        )
    return InteractionOutcome(
        status="degraded" if state.get("errors") else "completed",
        degradation_reasons=tuple(
            dict.fromkeys(
                error
                for error in state.get("errors", [])
                if isinstance(error, str) and error.isascii() and error.replace("_", "").isalnum()
            )
        ),
        session_id=session_id,
        interaction_id=interaction_id,
        public_request_id=public_request_id,
        response=response,
    )


def _hide_report_artifact_refs(text: str, state: dict[str, Any]) -> str:
    """Remove executor-only artifact references from text returned to the user."""
    references_by_task = state.get("report_artifact_refs")
    if not isinstance(references_by_task, dict):
        return text
    references: set[str] = set()
    for value in references_by_task.values():
        if isinstance(value, str):
            references.add(value)
        elif isinstance(value, (tuple, list, set)):
            references.update(item for item in value if isinstance(item, str))
    for reference in references:
        if reference:
            text = re.sub(re.escape(reference), "[referência interna]", text, flags=re.IGNORECASE)
    return text


def _assistant_message(
    *,
    session_id: UUID,
    interaction_id: UUID,
    response: InteractionOutcome,
    created_at: datetime,
) -> ConversationMessage:
    is_question = isinstance(response.response, QuestionResult)
    content = response.response.prompt if is_question else response.response.text
    return ConversationMessage(
        message_id=uuid5(interaction_id, "assistant"),
        session_id=session_id,
        interaction_id=interaction_id,
        sequence=1,
        role=MessageRole.ASSISTANT,
        kind=MessageKind.CLARIFICATION_QUESTION if is_question else MessageKind.ASSISTANT_ANSWER,
        sanitized_content=minimize_pii(content),
        created_at=created_at,
        payload_json=response.model_dump_json(),
    )
