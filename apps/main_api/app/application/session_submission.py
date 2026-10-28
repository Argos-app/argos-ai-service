"""Aceite, deduplicação e lease de interações de sessão."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from app.application.contracts.interactions import (
    AnswerCommand,
    InteractionCommand,
    InteractionOutcome,
)
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.execution_limits import ExecutionLimits
from app.application.identity import ActorContext
from app.application.operation_observation import observe_operation
from app.application.session_execution import SessionExecution
from app.application.session_helpers import (
    SAFE_ANSWER,
    _authorized_farm_ids,
    _has_full_farm_access,
    _interaction_id,
    _mongo_utcnow,
    _normalize_input,
)
from app.application.session_recovery import SessionRecovery
from app.application.session_service_types import (
    _AcceptedInteraction,
    _InteractionRequest,
)
from app.application.verified_evidence import verified_evidence_fallback
from app.domain.sessions import (
    ConversationMessage,
    InteractionRecord,
    InteractionStatus,
    MessageKind,
    MessageRole,
    ProcessingLease,
)
from app.ports.conversation_flow import ConversationFlow
from app.ports.interaction_repository import InteractionRepository
from app.ports.session_repository import SessionRepository
from app.ports.session_work_repository import SessionWorkRepository
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)

logger = logging.getLogger("app.application.session_service")


class SessionSubmission:
    def __init__(
        self,
        *,
        background_task_scheduler: Callable[[Coroutine[Any, Any, Any]], None] | None,
        execution_max_model_calls: int,
        execution_max_tool_calls: int,
        graph_provider: Callable[[], ConversationFlow | None],
        interactions: InteractionRepository,
        lease_seconds: int,
        observation_collector: ObservationCollector | None,
        session_work: SessionWorkRepository,
        sessions: SessionRepository,
        execution: SessionExecution,
        recovery: SessionRecovery,
    ) -> None:
        self._background_task_scheduler = background_task_scheduler
        self._execution_max_model_calls = execution_max_model_calls
        self._execution_max_tool_calls = execution_max_tool_calls
        self._graph_provider = graph_provider
        self._interactions = interactions
        self._lease_seconds = lease_seconds
        self._observation_collector = observation_collector
        self._session_work = session_work
        self._sessions = sessions
        self._execution = execution
        self._recovery = recovery

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
            context = await self._execution._build_interaction_context(accepted)
            state = await self._execution._execute_interaction(accepted, context, execution_limits)
        except TimeoutError as exc:
            logger.warning(
                "a interação da sessão excedeu o tempo limite de execução",
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
                "falha no grafo de interação da sessão",
                extra={"error_type": type(exc).__name__},
            )
            state = {"final_answer": SAFE_ANSWER, "errors": ["graph_execution_failed"]}
            failed = True
        return await self._execution._finalize_interaction(accepted, state, failed=failed)

    @asynccontextmanager
    async def _accept_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> AsyncIterator[_AcceptedInteraction | InteractionOutcome]:
        """O MongoDB armazena datas BSON com precisão de milissegundos. Mantenha
        essa precisão nos horários persistidos para que uma conclusão rápida
        não seja arredondada para antes da criação da interação."""
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
        existing = await self._recovery._find_existing(
            session_id,
            principal.user_id,
            kind=kind,
            deduplication_key=deduplication_key,
            input_hash=input_hash,
        )
        if existing is not None:
            yield await self._recovery._replay_or_reject(existing, session)
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
            ) = await self._recovery._validate_answer(
                session, body, graph, new_recipient_selectors=recipient_selectors
            )

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
            self._recovery._renew_lease(session_id, lease, stop_renewal, lease_lost)
        )
        try:
            existing = await self._recovery._find_existing(
                session_id,
                principal.user_id,
                kind=kind,
                deduplication_key=deduplication_key,
                input_hash=input_hash,
            )
            if existing is not None:
                yield await self._recovery._replay_or_reject(existing, session)
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
                    duplicate = await self._recovery._find_existing(
                        session_id,
                        principal.user_id,
                        kind=kind,
                        deduplication_key=deduplication_key,
                        input_hash=input_hash,
                    )
                    if duplicate is not None:
                        yield await self._recovery._replay_or_reject(duplicate, session)
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
