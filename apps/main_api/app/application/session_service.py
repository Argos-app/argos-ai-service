"""Casos de uso de sessões e processamento síncrono de interações."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException, status

from app.api.v1.dependencies.auth import Principal
from app.api.v1.models.interactions import (
    AnswerChoice,
    AnswerConfirmation,
    AnswerInteraction,
    AnswerResponse,
    AnswerText,
    InteractionInput,
    InteractionResult,
    MessageInteraction,
    QuestionOption,
    QuestionResponse,
)
from app.api.v1.models.sessions import Session
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
from app.ports.interaction_repository import InteractionRepository
from app.ports.session_repository import SessionRepository
from app.ports.session_work_repository import SessionWorkRepository
from app.privacy import minimize_pii
from app.session_config import (
    CLARIFICATION_TTL_SECONDS,
    SESSION_LEASE_RENEW_SECONDS,
    SESSION_LEASE_SECONDS,
)

GraphProvider = Callable[[], Any]
SAFE_ANSWER = "Não tenho informação suficiente para responder com segurança agora."


class MongoSessionService:
    """Executa interações na requisição HTTP e persiste cada transição."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        interactions: InteractionRepository,
        conversations: ConversationRepository,
        session_work: SessionWorkRepository,
        graph_provider: GraphProvider,
        lease_seconds: int = SESSION_LEASE_SECONDS,
        renew_seconds: int = SESSION_LEASE_RENEW_SECONDS,
    ) -> None:
        self._sessions = sessions
        self._interactions = interactions
        self._conversations = conversations
        self._session_work = session_work
        self._graph_provider = graph_provider
        self._lease_seconds = lease_seconds
        self._renew_seconds = renew_seconds

    async def create_session(self, principal: Principal) -> Session:
        if not principal.farm_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="O usuário não possui fazendas autorizadas",
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

    async def list_sessions(self, principal: Principal) -> list[Session]:
        return [
            response
            for record in await self._sessions.list_owned(principal.user_id)
            if (response := _session_response(record, principal.farm_ids)) is not None
        ]

    async def create_interaction(
        self,
        principal: Principal,
        session_id: UUID,
        body: InteractionInput,
    ) -> InteractionResult:
        session = await self._sessions.get_owned(session_id, principal.user_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Sessão não encontrada"
            )
        authorized_farms = _authorized_farm_ids(session, principal.farm_ids)
        if not authorized_farms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Nenhuma fazenda desta sessão segue autorizada",
            )

        kind, deduplication_key, sanitized_input, payload_json, input_hash, public_request_id = (
            _normalize_input(body)
        )
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
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Modelos de interação não configurados",
            )

        answer_text: str | None = None
        if isinstance(body, AnswerInteraction):
            answer_text = await self._validate_answer(session, body, graph)

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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Outra interação está processando esta sessão",
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
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A posse da sessão mudou antes da aceitação",
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
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A interação mudou durante o processamento",
                )

            failed = False
            try:
                from app.graph.workflow import (
                    executar_fluxo,
                    executar_resposta,
                )

                if isinstance(body, MessageInteraction):
                    state = await executar_fluxo(
                        graph,
                        sanitized_input,
                        user_id=str(principal.user_id),
                        farm_id=str(authorized_farms[0]),
                        session_id=str(session_id),
                        interaction_id=str(interaction_id),
                    )
                else:
                    state = await executar_resposta(
                        graph,
                        answer_text or sanitized_input,
                        question_id=str(body.question_id),
                        session_id=str(session_id),
                        interaction_id=str(interaction_id),
                    )
            except Exception:
                failed = True
                state = {"final_answer": SAFE_ANSWER}

            if lease_lost.is_set():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A posse da sessão expirou durante o processamento",
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
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A posse da sessão mudou antes da finalização",
                )
            return response
        finally:
            stop_renewal.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self._sessions.release_lease(session_id, lease)

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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A chave da requisição já foi usada com outro conteúdo",
            )
        return existing

    async def _replay_or_reject(
        self, interaction: InteractionRecord, session: SessionRecord
    ) -> InteractionResult:
        if interaction.status in {InteractionStatus.COMPLETED, InteractionStatus.FAILED}:
            if interaction.result_json:
                return InteractionResult.model_validate_json(interaction.result_json)
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A interação anterior foi interrompida e não será reexecutada",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A interação está em andamento ou foi interrompida",
        )

    async def _validate_answer(
        self, session: SessionRecord, body: AnswerInteraction, graph: Any
    ) -> str:
        source = await self._interactions.get_owned(body.interaction_id, session.user_id)
        if (
            source is None
            or source.session_id != session.session_id
            or source.status != InteractionStatus.COMPLETED
            or not source.result_json
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pergunta inválida")
        try:
            source_result = InteractionResult.model_validate_json(source.result_json)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Pergunta inválida"
            ) from exc
        question = source_result.response
        now = _utcnow()
        if (
            not isinstance(question, QuestionResponse)
            or question.question_id != body.question_id
            or question.expires_at <= now
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pergunta não está mais pendente",
            )
        if isinstance(body.answer, AnswerText):
            if question.input_type != "free_text":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Tipo de resposta incompatível",
                )
            graph_answer = minimize_pii(body.answer.text)
        elif isinstance(body.answer, AnswerChoice):
            if question.input_type != "single_choice":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Tipo de resposta incompatível",
                )
            option = next(
                (item for item in question.options if item.option_id == body.answer.option_id), None
            )
            if option is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Opção inválida"
                )
            graph_answer = option.label
        elif isinstance(body.answer, AnswerConfirmation):
            if question.input_type != "confirmation":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Tipo de resposta incompatível",
                )
            graph_answer = "Sim" if body.answer.confirm else "Não"
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Resposta inválida"
            )

        try:
            snapshot = await graph.aget_state(
                {"configurable": {"thread_id": str(session.session_id)}}
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="O estado da pergunta não está disponível para retomada",
            ) from exc
        if getattr(snapshot, "values", {}).get("question_id") != str(body.question_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pergunta não corresponde ao estado atual da sessão",
            )
        return graph_answer

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


def _authorized_farm_ids(session: SessionRecord, current_farms: list[UUID]) -> tuple[UUID, ...]:
    current = set(current_farms)
    return tuple(farm_id for farm_id in session.farm_ids if farm_id in current)


def _session_response(session: SessionRecord, current_farms: list[UUID]) -> Session | None:
    farm_ids = _authorized_farm_ids(session, current_farms)
    if not farm_ids:
        return None
    return Session(
        session_id=session.session_id,
        farm_ids=list(farm_ids),
        created_at=session.created_at,
    )


def _normalize_input(
    body: InteractionInput,
) -> tuple[InteractionKind, str, str, str, str, UUID]:
    if isinstance(body, MessageInteraction):
        sanitized = minimize_pii(body.message)
        if not sanitized.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A mensagem não pode estar vazia",
            )
        payload = {"kind": "message", "message": sanitized}
        key = str(body.client_message_id)
        public_id = body.client_message_id
        kind = InteractionKind.MESSAGE
        content = sanitized
    else:
        answer = body.answer.model_dump(mode="json")
        if isinstance(body.answer, AnswerText):
            answer["text"] = minimize_pii(body.answer.text)
            content = answer["text"]
        elif isinstance(body.answer, AnswerChoice):
            content = f"Opção selecionada: {body.answer.option_id}"
        else:
            content = "Sim" if body.answer.confirm else "Não"
        if not content.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A resposta não pode estar vazia",
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
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return kind, key, content, payload_json, digest, public_id


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
) -> InteractionResult:
    request = state.get("clarification_request")
    prompt = state.get("clarification_question")
    question_id = state.get("question_id")
    if request is not None and prompt and question_id:
        response = QuestionResponse(
            kind="question",
            question_id=UUID(str(question_id)),
            input_type=request.input_type,
            prompt=minimize_pii(prompt),
            reason_code=request.reason_code,
            options=[QuestionOption.model_validate(item.model_dump()) for item in request.options],
            expires_at=now + timedelta(seconds=CLARIFICATION_TTL_SECONDS),
        )
    else:
        response = AnswerResponse(
            kind="answer",
            text=minimize_pii(state.get("final_answer") or SAFE_ANSWER),
        )
    return InteractionResult(
        session_id=session_id,
        interaction_id=interaction_id,
        public_request_id=public_request_id,
        response=response,
    )


def _assistant_message(
    *,
    session_id: UUID,
    interaction_id: UUID,
    response: InteractionResult,
    created_at: datetime,
) -> ConversationMessage:
    is_question = isinstance(response.response, QuestionResponse)
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
