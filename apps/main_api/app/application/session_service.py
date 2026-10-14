"""Casos de uso de sessões e processamento síncrono de interações."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.application.context_builder import build_session_context
from app.application.contracts.interactions import (
    AnswerCommand,
    AnswerResult,
    ChoiceAnswer,
    ConfirmationAnswer,
    InteractionCommand,
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
from packages.contracts import RecipientSelector
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
    current_correlation_id,
)

GraphProvider = Callable[[], Any]
SummaryGeneratorProvider = Callable[[], SessionSummaryGenerator | None]
MemoryExtractorProvider = Callable[[], UserMemoryExtractor | None]
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."
logger = logging.getLogger(__name__)


class SessionApplicationService:
    """Executa interações e persiste cada transição usando portas injetadas."""

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
        now = _utcnow()
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

    async def create_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> InteractionOutcome:
        if self._observation_collector is None:
            return await self._create_interaction(principal, session_id, body)
        async with observe_operation(
            self._observation_collector,
            operation_kind=OperationKind.INTERACTION,
            operation_name="interaction.process",
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.LOCAL_OPERATION,
            error_origin=ErrorOrigin.APPLICATION,
        ) as recorder:
            result = await self._create_interaction(principal, session_id, body)
            recorder.finish(
                result=ResultMetadata(
                    source="application",
                    code=result.response.kind,
                )
            )
            return result

    async def _create_interaction(
        self,
        principal: ActorContext,
        session_id: UUID,
        body: InteractionCommand,
    ) -> InteractionOutcome:
        session = await self._sessions.get_owned(session_id, principal.user_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Sessão não encontrada")
        authorized_farms = _authorized_farm_ids(session, principal.farm_ids)
        if not authorized_farms:
            raise ApplicationError(
                ApplicationErrorCode.FORBIDDEN, "Nenhuma fazenda desta sessão segue autorizada"
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
            return await self._replay_or_reject(existing, session)

        graph = self._graph_provider()
        if graph is None:
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "Modelos de interação não configurados"
            )

        answer_text: str | None = None
        send_intent_source_interaction_id: str | None = None
        if isinstance(body, AnswerCommand):
            (
                answer_text,
                recipient_selectors,
                send_intent_requested,
                send_intent_source_interaction_id,
            ) = await self._validate_answer(
                session, body, graph, new_recipient_selectors=recipient_selectors
            )

        lease_owner = uuid4()
        now = _utcnow()
        lease = await self._sessions.acquire_lease(
            session_id,
            principal.user_id,
            owner_id=lease_owner,
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
                return await self._replay_or_reject(existing, session)

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
                        return await self._replay_or_reject(duplicate, session)
                raise
            if accepted_message is None:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "A posse da sessão mudou antes da aceitação"
                )

            running = replace(
                interaction,
                status=InteractionStatus.RUNNING,
                updated_at=_utcnow(),
                version=interaction.version + 1,
            )
            if not await self._interactions.save_result(
                running, expected_version=interaction.version
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "A interação mudou durante o processamento"
                )

            failed = False
            try:
                memory_input = _memory_input(body, sanitized_input, answer_text)
                if memory_input is not None:
                    await self._update_user_memory(
                        user_id=principal.user_id,
                        session_id=session_id,
                        interaction_id=interaction_id,
                        source_created_at=accepted_message.created_at,
                        sanitized_user_input=memory_input,
                    )
                await self._publish_due_summary(
                    session_id=session_id,
                    user_id=principal.user_id,
                    user_message_count=(accepted_message.user_message_number or 1) - 1,
                    through_sequence=accepted_message.sequence - 1,
                )
                session_context = await build_session_context(
                    session_id=session_id,
                    user_id=principal.user_id,
                    before_sequence=accepted_message.sequence,
                    conversations=self._conversations,
                    interactions=self._interactions,
                    summaries=self._summaries,
                    user_memory=self._user_memory,
                )
                from app.graph.workflow import (
                    executar_fluxo,
                    executar_resposta,
                )

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
                    )
                    if self._observation_collector is not None
                    else None
                )
                try:
                    execution_limits = ExecutionLimits(
                        max_model_calls=self._execution_max_model_calls,
                        max_tool_calls=self._execution_max_tool_calls,
                    )
                    graph_config = {
                        "configurable": {
                            "argos_user_id": str(principal.user_id),
                            "argos_authorized_property_ids": tuple(
                                str(farm_id) for farm_id in authorized_farms
                            ),
                            "argos_request_id": str(current_correlation_id() or interaction_id),
                        }
                    }
                    if self._langsmith_callback is not None:
                        graph_config["callbacks"] = [self._langsmith_callback]
                        graph_config["metadata"] = {"argos_operation": "session.interaction"}
                    if isinstance(body, MessageCommand):
                        with execution_limits_scope(execution_limits):
                            async with asyncio.timeout(self._execution_timeout_seconds):
                                state = await executar_fluxo(
                                    graph,
                                    sanitized_input,
                                    user_id=str(principal.user_id),
                                    farm_id=str(authorized_farms[0]),
                                    session_id=str(session_id),
                                    interaction_id=str(interaction_id),
                                    session_context=session_context,
                                    recipient_selectors=recipient_selectors,
                                    send_intent_requested=send_intent_requested,
                                    config=graph_config,
                                )
                    else:
                        with execution_limits_scope(execution_limits):
                            async with asyncio.timeout(self._execution_timeout_seconds):
                                state = await executar_resposta(
                                    graph,
                                    answer_text or sanitized_input,
                                    question_id=str(body.question_id),
                                    session_id=str(session_id),
                                    interaction_id=str(interaction_id),
                                    checkpoint_namespace=str(body.interaction_id),
                                    session_context=session_context,
                                    recipient_selectors=recipient_selectors,
                                    send_intent_requested=send_intent_requested,
                                    send_intent_source_interaction_id=send_intent_source_interaction_id,
                                    config=graph_config,
                                )
                except BaseException as exc:
                    if graph_recorder is not None:
                        graph_recorder.finish(error=exc, error_origin=ErrorOrigin.APPLICATION)
                    raise
                else:
                    if graph_recorder is not None:
                        graph_recorder.finish()
            except Exception as exc:
                logger.error(
                    "session interaction graph failed",
                    extra={"error_type": type(exc).__name__},
                )
                failed = True
                state = {"final_answer": SAFE_ANSWER}

            if lease_lost.is_set():
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "A posse da sessão expirou durante o processamento",
                )

            response = _response_from_state(
                session_id=session_id,
                interaction_id=interaction_id,
                public_request_id=public_request_id,
                state=state,
                now=_utcnow(),
            )
            result_json = response.model_dump_json()
            finished_at = _utcnow()
            completed = replace(
                running,
                status=InteractionStatus.FAILED if failed else InteractionStatus.COMPLETED,
                result_json=result_json,
                updated_at=finished_at,
                version=running.version + 1,
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
                lease,
                expected_version=running.version,
                now=finished_at,
            )
            if committed_message is None:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "A posse da sessão mudou antes da finalização"
                )
            await self._publish_due_summary(
                session_id=session_id,
                user_id=principal.user_id,
                user_message_count=accepted_message.user_message_number or 0,
                through_sequence=committed_message.sequence,
            )
            return response
        finally:
            stop_renewal.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self._sessions.release_lease(session_id, lease)

    async def _publish_due_summary(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        user_message_count: int,
        through_sequence: int,
    ) -> None:
        if self._summary_service is None:
            return
        try:
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
        if interaction.status in {InteractionStatus.COMPLETED, InteractionStatus.FAILED}:
            if interaction.result_json:
                return InteractionOutcome.model_validate_json(interaction.result_json)
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
    ) -> tuple[str, dict[str, dict[str, str]], bool, str | None]:
        source = await self._interactions.get_owned(body.interaction_id, session.user_id)
        if (
            source is None
            or source.session_id != session.session_id
            or source.status != InteractionStatus.COMPLETED
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
                        "checkpoint_ns": f"interaction-{body.interaction_id}",
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
                renewed = await self._sessions.renew_lease(
                    session_id,
                    lease,
                    now=now,
                    expires_at=now + timedelta(seconds=self._lease_seconds),
                )
                if not renewed:
                    lease_lost.set()
                    return


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _authorized_farm_ids(
    session: SessionRecord, current_farms: Collection[UUID]
) -> tuple[UUID, ...]:
    current = set(current_farms)
    return tuple(farm_id for farm_id in session.farm_ids if farm_id in current)


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
                prompt=minimize_pii(prompt),
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
            text=minimize_pii(state.get("final_answer") or SAFE_ANSWER),
        )
    return InteractionOutcome(
        session_id=session_id,
        interaction_id=interaction_id,
        public_request_id=public_request_id,
        response=response,
    )


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
