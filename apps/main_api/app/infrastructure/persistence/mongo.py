"""Adapters Mongo para sessões, interações, conversas e memórias."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.memory import SessionSummary, SummaryFact, UserMemory
from app.domain.sessions import (
    ConversationMessage,
    ExecutionEvent,
    ExecutionMessage,
    ExecutionMessageRole,
    ExecutionStatus,
    ExecutionTarget,
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    MessageKind,
    MessageRole,
    ProcessingLease,
    SessionRecord,
)
from app.infrastructure.persistence.errors import PersistenceConflictError


def _is_duplicate(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 11000


def _session_document(session: SessionRecord) -> dict[str, Any]:
    lease = session.processing_lease
    return {
        "_id": str(session.session_id),
        "user_id": str(session.user_id),
        "farm_ids": [str(item) for item in session.farm_ids],
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "user_message_count": session.user_message_count,
        "next_message_sequence": session.next_message_sequence,
        "version": session.version,
        "lease_fencing_token": lease.fencing_token if lease else 0,
        "processing_lease": _lease_document(lease) if lease else None,
    }


def _lease_document(lease: ProcessingLease) -> dict[str, Any]:
    return {
        "owner_id": str(lease.owner_id),
        "fencing_token": lease.fencing_token,
        "acquired_at": lease.acquired_at,
        "expires_at": lease.expires_at,
    }


def _session_from_document(document: dict[str, Any]) -> SessionRecord:
    lease_doc = document.get("processing_lease")
    lease = (
        ProcessingLease(
            owner_id=UUID(lease_doc["owner_id"]),
            fencing_token=lease_doc["fencing_token"],
            acquired_at=lease_doc["acquired_at"],
            expires_at=lease_doc["expires_at"],
        )
        if lease_doc
        else None
    )
    return SessionRecord(
        session_id=UUID(document["_id"]),
        user_id=UUID(document["user_id"]),
        farm_ids=tuple(UUID(value) for value in document["farm_ids"]),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        user_message_count=document["user_message_count"],
        next_message_sequence=document["next_message_sequence"],
        version=document["version"],
        processing_lease=lease,
    )


class MongoSessionRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("user_id", 1), ("created_at", -1), ("_id", 1)],
            name="ix_sessions_owner_created",
        )

    async def create(self, session: SessionRecord) -> None:
        await self._collection.insert_one(_session_document(session))

    async def get_owned(self, session_id: UUID, user_id: UUID) -> SessionRecord | None:
        document = await self._collection.find_one(
            {"_id": str(session_id), "user_id": str(user_id)}
        )
        return _session_from_document(document) if document else None

    async def list_owned(self, user_id: UUID) -> list[SessionRecord]:
        cursor = self._collection.find({"user_id": str(user_id)}).sort(
            [("created_at", -1), ("_id", 1)]
        )
        return [_session_from_document(item) async for item in cursor]

    async def acquire_lease(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ProcessingLease | None:
        _validate_lease_times(now, expires_at)
        try:
            from pymongo import ReturnDocument
        except ImportError as exc:
            raise RuntimeError("instale pymongo via a dependência motor") from exc
        document = await self._collection.find_one_and_update(
            {
                "_id": str(session_id),
                "user_id": str(user_id),
                "$or": [
                    {"processing_lease": None},
                    {"processing_lease": {"$exists": False}},
                    {"processing_lease.expires_at": {"$lte": now}},
                ],
            },
            [
                {
                    "$set": {
                        "lease_fencing_token": {
                            "$add": [{"$ifNull": ["$lease_fencing_token", 0]}, 1]
                        },
                        "processing_lease": {
                            "owner_id": str(owner_id),
                            "fencing_token": {
                                "$add": [{"$ifNull": ["$lease_fencing_token", 0]}, 1]
                            },
                            "acquired_at": now,
                            "expires_at": expires_at,
                        },
                        "updated_at": now,
                        "version": {"$add": ["$version", 1]},
                    }
                }
            ],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        lease = document["processing_lease"]
        return ProcessingLease(
            owner_id=UUID(lease["owner_id"]),
            fencing_token=document["lease_fencing_token"],
            acquired_at=lease["acquired_at"],
            expires_at=lease["expires_at"],
        )

    async def renew_lease(
        self,
        session_id: UUID,
        lease: ProcessingLease,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        _validate_lease_times(now, expires_at)
        result = await self._collection.update_one(
            _lease_filter(session_id, lease) | {"processing_lease.expires_at": {"$gt": now}},
            {
                "$set": {
                    "processing_lease.expires_at": expires_at,
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
        )
        return result.matched_count == 1

    async def release_lease(self, session_id: UUID, lease: ProcessingLease) -> bool:
        result = await self._collection.update_one(
            _lease_filter(session_id, lease),
            {
                "$set": {"processing_lease": None, "updated_at": datetime.now(UTC)},
                "$inc": {"version": 1},
            },
        )
        return result.matched_count == 1


def _validate_lease_times(now: datetime, expires_at: datetime) -> None:
    if (
        now.tzinfo is None
        or expires_at.tzinfo is None
        or now.utcoffset() != timedelta(0)
        or expires_at.utcoffset() != timedelta(0)
        or expires_at <= now
    ):
        raise ValueError("lease exige timestamps UTC e expires_at posterior a now")


def _lease_filter(session_id: UUID, lease: ProcessingLease) -> dict[str, Any]:
    return {
        "_id": str(session_id),
        "processing_lease.owner_id": str(lease.owner_id),
        "processing_lease.fencing_token": lease.fencing_token,
    }


def _interaction_document(interaction: InteractionRecord) -> dict[str, Any]:
    return {
        "_id": str(interaction.interaction_id),
        "session_id": str(interaction.session_id),
        "user_id": str(interaction.user_id),
        "deduplication_key": interaction.deduplication_key,
        "kind": interaction.kind.value,
        "sanitized_input": interaction.sanitized_input,
        "input_hash": interaction.input_hash,
        "status": interaction.status.value,
        "created_at": interaction.created_at,
        "updated_at": interaction.updated_at,
        "result_json": interaction.result_json,
        "version": interaction.version,
    }


def _interaction_from_document(document: dict[str, Any]) -> InteractionRecord:
    return InteractionRecord(
        interaction_id=UUID(document["_id"]),
        session_id=UUID(document["session_id"]),
        user_id=UUID(document["user_id"]),
        deduplication_key=document["deduplication_key"],
        kind=InteractionKind(document["kind"]),
        sanitized_input=document["sanitized_input"],
        input_hash=document["input_hash"],
        status=InteractionStatus(document["status"]),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        result_json=document.get("result_json"),
        version=document["version"],
    )


class MongoInteractionRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("session_id", 1), ("kind", 1), ("deduplication_key", 1)],
            unique=True,
            name="uq_interaction_deduplication",
        )
        await self._collection.create_index(
            [("session_id", 1), ("status", 1), ("updated_at", -1)],
            name="ix_interaction_session_status",
        )

    async def create_or_get(self, interaction: InteractionRecord) -> tuple[InteractionRecord, bool]:
        document = _interaction_document(interaction)
        try:
            await self._collection.insert_one(document)
            return interaction, True
        except Exception as exc:
            if not _is_duplicate(exc):
                raise
        existing = await self._collection.find_one(
            {
                "session_id": str(interaction.session_id),
                "kind": interaction.kind.value,
                "deduplication_key": interaction.deduplication_key,
            }
        )
        if existing is None:
            raise PersistenceConflictError("chave de interação duplicada sem registro recuperável")
        stored = _interaction_from_document(existing)
        if stored.input_hash != interaction.input_hash:
            raise PersistenceConflictError("chave de interação reutilizada com outro conteúdo")
        return stored, False

    async def get_owned(self, interaction_id: UUID, user_id: UUID) -> InteractionRecord | None:
        document = await self._collection.find_one(
            {"_id": str(interaction_id), "user_id": str(user_id)}
        )
        return _interaction_from_document(document) if document else None

    async def find_by_deduplication_key(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        kind: InteractionKind,
        deduplication_key: str,
    ) -> InteractionRecord | None:
        document = await self._collection.find_one(
            {
                "session_id": str(session_id),
                "user_id": str(user_id),
                "kind": kind.value,
                "deduplication_key": deduplication_key,
            }
        )
        return _interaction_from_document(document) if document else None

    async def save_result(self, interaction: InteractionRecord, *, expected_version: int) -> bool:
        if interaction.version != expected_version + 1:
            raise ValueError("version da interação deve avançar exatamente uma unidade")
        update = await self._collection.update_one(
            {"_id": str(interaction.interaction_id), "version": expected_version},
            {
                "$set": {
                    "status": interaction.status.value,
                    "result_json": interaction.result_json,
                    "updated_at": interaction.updated_at,
                    "version": interaction.version,
                }
            },
        )
        return update.matched_count == 1


def _message_document(message: ConversationMessage, user_id: UUID) -> dict[str, Any]:
    return {
        "_id": str(message.message_id),
        "session_id": str(message.session_id),
        "user_id": str(user_id),
        "interaction_id": str(message.interaction_id),
        "sequence": message.sequence,
        "role": message.role.value,
        "kind": message.kind.value,
        "sanitized_content": message.sanitized_content,
        "created_at": message.created_at,
        "user_message_number": message.user_message_number,
        "payload_json": message.payload_json,
    }


def _message_from_document(document: dict[str, Any]) -> ConversationMessage:
    return ConversationMessage(
        message_id=UUID(document["_id"]),
        session_id=UUID(document["session_id"]),
        interaction_id=UUID(document["interaction_id"]),
        sequence=document["sequence"],
        role=MessageRole(document["role"]),
        kind=MessageKind(document["kind"]),
        sanitized_content=document["sanitized_content"],
        created_at=document["created_at"],
        user_message_number=document.get("user_message_number"),
        payload_json=document.get("payload_json"),
    )


class MongoConversationRepository:
    def __init__(self, collection: Any, sessions: Any) -> None:
        self._collection = collection
        self._sessions = sessions

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("session_id", 1), ("sequence", 1)],
            unique=True,
            name="uq_message_session_sequence",
        )
        await self._collection.create_index(
            [("user_id", 1), ("session_id", 1), ("sequence", -1)],
            name="ix_message_owner_session_sequence",
        )

    async def append(self, message: ConversationMessage) -> None:
        session = await self._sessions.find_one({"_id": str(message.session_id)})
        if session is None:
            raise KeyError(f"sessão não encontrada: {message.session_id}")
        document = _message_document(message, UUID(session["user_id"]))
        try:
            await self._collection.insert_one(document)
        except Exception as exc:
            if not _is_duplicate(exc):
                raise
            existing = await self._collection.find_one(
                {"session_id": str(message.session_id), "sequence": message.sequence}
            )
            if existing != document:
                raise PersistenceConflictError("sequência de mensagem já ocupada") from exc

    async def list_before_sequence(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:
        # A API solicita uma linha extra para calcular has_more sem contagem.
        if not 1 <= limit <= 101:
            raise ValueError("limit deve estar entre 1 e 101")
        query: dict[str, Any] = {"session_id": str(session_id), "user_id": str(user_id)}
        if before_sequence is not None:
            query["sequence"] = {"$lt": before_sequence}
        cursor = self._collection.find(query).sort("sequence", -1).limit(limit)
        return tuple([_message_from_document(item) async for item in cursor])

    async def list_after_sequence(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:
        if after_sequence < 0 or through_sequence < after_sequence or not 1 <= limit <= 100:
            raise ValueError("intervalo ou limit inválido")
        cursor = (
            self._collection.find(
                {
                    "session_id": str(session_id),
                    "user_id": str(user_id),
                    "sequence": {"$gt": after_sequence, "$lte": through_sequence},
                }
            )
            .sort("sequence", 1)
            .limit(limit)
        )
        return tuple([_message_from_document(item) async for item in cursor])


def _event_document(event: ExecutionEvent, user_id: UUID) -> dict[str, Any]:
    return {
        "_id": str(event.event_id),
        "invocation_id": str(event.invocation_id),
        "session_id": str(event.session_id),
        "interaction_id": str(event.interaction_id),
        "user_id": str(user_id),
        "target": event.target.value,
        "target_name": event.target_name,
        "status": event.status.value,
        "created_at": event.created_at,
        "attempt": event.attempt,
        "sanitized_input": event.sanitized_input,
        "sanitized_output": event.sanitized_output,
        "messages": [
            {
                "role": item.role.value,
                "sanitized_content": item.sanitized_content,
                "tool_name": item.tool_name,
                "tool_call_id": item.tool_call_id,
            }
            for item in event.messages
        ],
        "safe_error_code": event.safe_error_code,
        "completed_at": event.completed_at,
    }


def _event_from_document(document: dict[str, Any]) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=UUID(document["_id"]),
        invocation_id=UUID(document["invocation_id"]),
        session_id=UUID(document["session_id"]),
        interaction_id=UUID(document["interaction_id"]),
        target=ExecutionTarget(document["target"]),
        target_name=document["target_name"],
        status=ExecutionStatus(document["status"]),
        created_at=document["created_at"],
        attempt=document.get("attempt", 1),
        sanitized_input=document.get("sanitized_input"),
        sanitized_output=document.get("sanitized_output"),
        messages=tuple(
            ExecutionMessage(
                role=ExecutionMessageRole(item["role"]),
                sanitized_content=item["sanitized_content"],
                tool_name=item.get("tool_name"),
                tool_call_id=item.get("tool_call_id"),
            )
            for item in document.get("messages", [])
        ),
        safe_error_code=document.get("safe_error_code"),
        completed_at=document.get("completed_at"),
    )


class MongoExecutionEventRepository:
    def __init__(self, collection: Any, sessions: Any) -> None:
        self._collection = collection
        self._sessions = sessions

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("session_id", 1), ("interaction_id", 1), ("created_at", 1)],
            name="ix_execution_session_interaction",
        )
        await self._collection.create_index(
            [("session_id", 1), ("interaction_id", 1), ("invocation_id", 1)],
            name="ix_execution_invocation_correlation",
        )

    async def append(self, event: ExecutionEvent) -> None:
        session = await self._sessions.find_one({"_id": str(event.session_id)})
        if session is None:
            raise KeyError(f"sessão não encontrada: {event.session_id}")
        document = _event_document(event, UUID(session["user_id"]))
        try:
            await self._collection.insert_one(document)
        except Exception as exc:
            if not _is_duplicate(exc):
                raise
            existing = await self._collection.find_one({"_id": str(event.event_id)})
            if existing != document:
                raise PersistenceConflictError("event_id já usado com outro evento") from exc

    async def list_for_interactions(
        self,
        session_id: UUID,
        user_id: UUID,
        interaction_ids: Sequence[UUID],
    ) -> tuple[ExecutionEvent, ...]:
        if not interaction_ids:
            return ()
        cursor = self._collection.find(
            {
                "session_id": str(session_id),
                "user_id": str(user_id),
                "interaction_id": {"$in": [str(item) for item in interaction_ids]},
                "status": {"$in": [ExecutionStatus.SUCCEEDED.value, ExecutionStatus.FAILED.value]},
            }
        ).sort([("created_at", 1), ("_id", 1)])
        return tuple([_event_from_document(item) async for item in cursor])


def _summary_document(summary: SessionSummary) -> dict[str, Any]:
    return {
        "_id": str(summary.session_id),
        "revision": summary.revision,
        "through_sequence": summary.through_sequence,
        "through_user_message_number": summary.through_user_message_number,
        "activity": summary.activity,
        "important_facts": [
            {"content": fact.content, "source_references": list(fact.source_references)}
            for fact in summary.important_facts
        ],
        "decisions": list(summary.decisions),
        "pending_activity": summary.pending_activity,
        "limitations": list(summary.limitations),
        "summary_text": summary.summary_text,
        "token_count": summary.token_count,
        "updated_at": summary.updated_at,
    }


def _summary_from_document(document: dict[str, Any]) -> SessionSummary:
    return SessionSummary(
        session_id=UUID(document["_id"]),
        revision=document["revision"],
        through_sequence=document["through_sequence"],
        through_user_message_number=document["through_user_message_number"],
        activity=document["activity"],
        important_facts=tuple(
            SummaryFact(item["content"], tuple(item.get("source_references", [])))
            for item in document["important_facts"]
        ),
        decisions=tuple(document["decisions"]),
        pending_activity=document.get("pending_activity"),
        limitations=tuple(document["limitations"]),
        summary_text=document["summary_text"],
        token_count=document["token_count"],
        updated_at=document["updated_at"],
    )


class MongoSessionSummaryRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        # _id is the unique session key; this collection keeps only the current revision.
        return None

    async def get_current(self, session_id: UUID) -> SessionSummary | None:
        document = await self._collection.find_one({"_id": str(session_id)})
        return _summary_from_document(document) if document else None

    async def save_if_current(
        self, summary: SessionSummary, *, expected_revision: int | None
    ) -> bool:
        document = _summary_document(summary)
        if expected_revision is None:
            try:
                await self._collection.insert_one(document)
                return True
            except Exception as exc:
                if _is_duplicate(exc):
                    return False
                raise
        if summary.revision != expected_revision + 1:
            raise ValueError("revision do resumo deve avançar exatamente uma unidade")
        result = await self._collection.replace_one(
            {"_id": str(summary.session_id), "revision": expected_revision}, document
        )
        return result.matched_count == 1


def _memory_document(memory: UserMemory) -> dict[str, Any]:
    return {
        "_id": str(memory.user_id),
        "tone": memory.tone,
        "general_goal": memory.general_goal,
        "tone_source_session_id": str(memory.tone_source_session_id)
        if memory.tone_source_session_id
        else None,
        "tone_source_interaction_id": str(memory.tone_source_interaction_id)
        if memory.tone_source_interaction_id
        else None,
        "goal_source_session_id": str(memory.goal_source_session_id)
        if memory.goal_source_session_id
        else None,
        "goal_source_interaction_id": str(memory.goal_source_interaction_id)
        if memory.goal_source_interaction_id
        else None,
        "tone_source_excerpt": memory.tone_source_excerpt,
        "goal_source_excerpt": memory.goal_source_excerpt,
        "tone_updated_at": memory.tone_updated_at,
        "goal_updated_at": memory.goal_updated_at,
        "revision": memory.revision,
        "updated_at": memory.updated_at,
    }


def _memory_from_document(document: dict[str, Any]) -> UserMemory:
    return UserMemory(
        user_id=UUID(document["_id"]),
        tone=document.get("tone"),
        general_goal=document.get("general_goal"),
        tone_source_session_id=UUID(document["tone_source_session_id"])
        if document.get("tone_source_session_id")
        else None,
        tone_source_interaction_id=UUID(document["tone_source_interaction_id"])
        if document.get("tone_source_interaction_id")
        else None,
        goal_source_session_id=UUID(document["goal_source_session_id"])
        if document.get("goal_source_session_id")
        else None,
        goal_source_interaction_id=UUID(document["goal_source_interaction_id"])
        if document.get("goal_source_interaction_id")
        else None,
        revision=document["revision"],
        updated_at=document["updated_at"],
        tone_source_excerpt=document.get("tone_source_excerpt"),
        goal_source_excerpt=document.get("goal_source_excerpt"),
        tone_updated_at=document.get("tone_updated_at"),
        goal_updated_at=document.get("goal_updated_at"),
    )


class MongoUserMemoryRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        return None

    async def get(self, user_id: UUID) -> UserMemory | None:
        document = await self._collection.find_one({"_id": str(user_id)})
        return _memory_from_document(document) if document else None

    async def save_if_current(self, memory: UserMemory, *, expected_revision: int | None) -> bool:
        document = _memory_document(memory)
        if expected_revision is None:
            try:
                await self._collection.insert_one(document)
                return True
            except Exception as exc:
                if _is_duplicate(exc):
                    return False
                raise
        if memory.revision != expected_revision + 1:
            raise ValueError("revision da memória deve avançar exatamente uma unidade")
        result = await self._collection.replace_one(
            {"_id": str(memory.user_id), "revision": expected_revision}, document
        )
        return result.matched_count == 1


class MongoSessionWorkRepository:
    """Unidade de trabalho transacional para aceitar/finalizar interações."""

    def __init__(
        self,
        database: Any,
        sessions: Any,
        interactions: Any,
        messages: Any,
    ) -> None:
        self._client = database.client
        self._sessions = sessions
        self._interactions = interactions
        self._messages = messages

    async def accept_user_interaction(
        self,
        interaction: InteractionRecord,
        message: ConversationMessage,
        lease: ProcessingLease,
        *,
        now: datetime,
    ) -> ConversationMessage | None:
        from pymongo import ReturnDocument

        query = _current_lease_filter(interaction.session_id, interaction.user_id, lease, now)
        async with await self._client.start_session() as mongo_session:
            async with mongo_session.start_transaction():
                session = await self._sessions.find_one_and_update(
                    query,
                    {
                        "$inc": {
                            "next_message_sequence": 1,
                            "user_message_count": 1,
                            "version": 1,
                        },
                        "$set": {"updated_at": now},
                    },
                    return_document=ReturnDocument.BEFORE,
                    session=mongo_session,
                )
                if session is None:
                    return None
                assigned_message = replace(
                    message,
                    sequence=session["next_message_sequence"],
                    user_message_number=session["user_message_count"] + 1,
                )
                await self._interactions.insert_one(
                    _interaction_document(interaction), session=mongo_session
                )
                await self._messages.insert_one(
                    _message_document(assigned_message, interaction.user_id),
                    session=mongo_session,
                )
        return assigned_message

    async def finalize_interaction(
        self,
        interaction: InteractionRecord,
        message: ConversationMessage,
        lease: ProcessingLease,
        *,
        expected_version: int,
        now: datetime,
    ) -> ConversationMessage | None:
        from pymongo import ReturnDocument

        query = _current_lease_filter(interaction.session_id, interaction.user_id, lease, now)
        async with await self._client.start_session() as mongo_session:
            async with mongo_session.start_transaction():
                session = await self._sessions.find_one_and_update(
                    query,
                    {
                        "$inc": {"next_message_sequence": 1, "version": 1},
                        "$set": {"updated_at": now},
                    },
                    return_document=ReturnDocument.BEFORE,
                    session=mongo_session,
                )
                if session is None:
                    return None
                result = await self._interactions.update_one(
                    {
                        "_id": str(interaction.interaction_id),
                        "user_id": str(interaction.user_id),
                        "version": expected_version,
                    },
                    {
                        "$set": {
                            "status": interaction.status.value,
                            "result_json": interaction.result_json,
                            "updated_at": interaction.updated_at,
                            "version": interaction.version,
                        }
                    },
                    session=mongo_session,
                )
                if result.matched_count != 1:
                    raise PersistenceConflictError("interação mudou durante a finalização")
                assigned_message = replace(message, sequence=session["next_message_sequence"])
                await self._messages.insert_one(
                    _message_document(assigned_message, interaction.user_id),
                    session=mongo_session,
                )
        return assigned_message


def _current_lease_filter(
    session_id: UUID,
    user_id: UUID,
    lease: ProcessingLease,
    now: datetime,
) -> dict[str, Any]:
    return {
        **_lease_filter(session_id, lease),
        "user_id": str(user_id),
        "processing_lease.expires_at": {"$gt": now},
    }


class MongoPersistence:
    """Conjunto injetável dos repositórios e coleções Mongo do serviço."""

    def __init__(self, database: Any) -> None:
        sessions = database["ai_sessions"]
        self.sessions = MongoSessionRepository(sessions)
        self.interactions = MongoInteractionRepository(database["ai_interactions"])
        self.conversations = MongoConversationRepository(database["ai_messages"], sessions)
        self.execution_events = MongoExecutionEventRepository(
            database["ai_execution_events"], sessions
        )
        self.session_summaries = MongoSessionSummaryRepository(database["ai_session_summaries"])
        self.user_memory = MongoUserMemoryRepository(database["ai_user_memory"])
        self.session_work = MongoSessionWorkRepository(
            database,
            sessions,
            database["ai_interactions"],
            database["ai_messages"],
        )

    async def ensure_indexes(self) -> None:
        for repository in (
            self.sessions,
            self.interactions,
            self.conversations,
            self.execution_events,
            self.session_summaries,
            self.user_memory,
        ):
            await repository.ensure_indexes()
