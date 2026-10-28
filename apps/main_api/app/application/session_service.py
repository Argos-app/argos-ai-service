"""Casos de uso de sessões e processamento síncrono de interações."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from app.application.contracts.interactions import (
    InteractionCommand,
    InteractionOperation,
    InteractionOutcome,
)
from app.application.contracts.sessions import SessionView
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.identity import ActorContext
from app.application.session_execution import SessionExecution
from app.application.session_helpers import (
    _authorized_farm_ids,
    _has_full_farm_access,
    _mongo_utcnow,
    _session_response,
    _utcnow,
)
from app.application.session_recovery import SessionRecovery
from app.application.session_submission import SessionSubmission
from app.application.session_summary_service import SessionSummaryService
from app.application.user_memory_service import UserMemoryService
from app.domain.sessions import (
    InteractionStatus,
    SessionRecord,
)
from app.ports.conversation_flow import ConversationFlow
from app.ports.conversation_repository import ConversationRepository
from app.ports.email_preparation_repository import EmailPreparationRepository
from app.ports.execution_event_repository import ExecutionEventRepository
from app.ports.interaction_repository import InteractionRepository
from app.ports.memory_generators import SessionSummaryGenerator, UserMemoryExtractor
from app.ports.session_repository import SessionRepository
from app.ports.session_summary_repository import SessionSummaryRepository
from app.ports.session_work_repository import SessionWorkRepository
from app.ports.user_memory_repository import UserMemoryRepository
from app.session_config import (
    SESSION_LEASE_RENEW_SECONDS,
    SESSION_LEASE_SECONDS,
)
from packages.observability import (
    ObservationCollector,
)

GraphProvider = Callable[[], ConversationFlow | None]
SummaryGeneratorProvider = Callable[[], SessionSummaryGenerator | None]
MemoryExtractorProvider = Callable[[], UserMemoryExtractor | None]
BackgroundTaskScheduler = Callable[[Coroutine[Any, Any, Any]], None]
logger = logging.getLogger(__name__)


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

        self._execution = SessionExecution(
            background_task_scheduler=self._background_task_scheduler,
            conversations=self._conversations,
            email_preparations=self._email_preparations,
            execution_timeout_seconds=self._execution_timeout_seconds,
            interactions=self._interactions,
            langsmith_callback=self._langsmith_callback,
            observation_collector=self._observation_collector,
            session_work=self._session_work,
            summaries=self._summaries,
            summary_service=self._summary_service,
            user_memory=self._user_memory,
            user_memory_service=self._user_memory_service,
        )

        self._recovery = SessionRecovery(
            email_preparations=self._email_preparations,
            interactions=self._interactions,
            lease_seconds=self._lease_seconds,
            observation_collector=self._observation_collector,
            renew_seconds=self._renew_seconds,
            sessions=self._sessions,
        )

        self._submission = SessionSubmission(
            background_task_scheduler=self._background_task_scheduler,
            execution_max_model_calls=self._execution_max_model_calls,
            execution_max_tool_calls=self._execution_max_tool_calls,
            graph_provider=self._graph_provider,
            interactions=self._interactions,
            lease_seconds=self._lease_seconds,
            observation_collector=self._observation_collector,
            session_work=self._session_work,
            sessions=self._sessions,
            execution=self._execution,
            recovery=self._recovery,
        )

    async def create_interaction(
        self, principal: ActorContext, session_id: UUID, body: InteractionCommand
    ) -> InteractionOutcome:
        return await self._submission.create_interaction(principal, session_id, body)

    async def submit_interaction(
        self, principal: ActorContext, session_id: UUID, body: InteractionCommand
    ) -> UUID:
        return await self._submission.submit_interaction(principal, session_id, body)

    async def create_session(self, principal: ActorContext) -> SessionView:
        """O MongoDB armazena datas BSON com precisão de milissegundos."""
        if not principal.farm_ids:
            raise ApplicationError(
                ApplicationErrorCode.FORBIDDEN, "O usuário não possui fazendas autorizadas"
            )

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


__all__ = ["SessionApplicationService"]
