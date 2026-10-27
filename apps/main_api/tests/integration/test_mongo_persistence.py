"""Verifica persistência, concorrência e isolamento usando Mongo replica set real."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.identity import ActorContext
from app.application.message_history_service import MessageHistoryService
from app.domain.email_preparations import EmailPreparation
from app.domain.memory import SessionSummary, SummaryFact, UserMemory
from app.domain.sessions import (
    ConversationMessage,
    ExecutionEvent,
    ExecutionStatus,
    ExecutionTarget,
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    MessageKind,
    MessageRole,
    SessionRecord,
)
from app.infrastructure.agents.execution_observer import ExecutionObserver
from app.infrastructure.persistence.email_preparations import MongoEmailPreparationRepository
from app.infrastructure.persistence.mongo import MongoPersistence
from motor.motor_asyncio import AsyncIOMotorClient

from packages.contracts import PreparedReport, PreparedReportStatus, RecipientRef

UTC_NOW = datetime.now(UTC)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execution_invocation_uses_one_document_and_retains_input():
    mongo_uri = os.getenv("MAIN_API_MONGO_TEST_URI")
    if not mongo_uri:
        pytest.skip("defina MAIN_API_MONGO_TEST_URI para executar a integração Mongo")
    client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    database_name = f"argos_execution_test_{uuid4().hex}"
    try:
        persistence = MongoPersistence(client[database_name])
        await persistence.ensure_indexes()
        now = datetime.now(UTC)
        session_id, user_id, interaction_id = uuid4(), uuid4(), uuid4()
        await persistence.sessions.create(
            SessionRecord(
                session_id=session_id,
                user_id=user_id,
                farm_ids=(uuid4(),),
                created_at=now,
                updated_at=now,
            )
        )
        observer = ExecutionObserver(
            persistence.execution_events, session_id=session_id, interaction_id=interaction_id
        )
        invocation_id = await observer.agent_started("farm", {"question": "teste"})
        started_document = await client[database_name]["ai_execution_events"].find_one(
            {"_id": str(invocation_id)}
        )
        await observer.agent_finished(invocation_id, "farm", output={"answer": "ok"})
        events = await persistence.execution_events.list_for_interactions(
            session_id, user_id, [interaction_id]
        )
        assert len(events) == 1
        assert events[0].created_at == started_document["created_at"]
        assert "teste" in events[0].sanitized_input
        assert "ok" in events[0].sanitized_output
        assert await client[database_name]["ai_execution_events"].count_documents({}) == 1
        await persistence.execution_events.append(
            replace(
                events[0],
                created_at=events[0].created_at + timedelta(seconds=2),
                completed_at=events[0].completed_at + timedelta(seconds=2),
            )
        )
        started = replace(
            events[0], status=ExecutionStatus.STARTED, sanitized_output=None, completed_at=None
        )
        await persistence.execution_events.append(started)
        assert await client[database_name]["ai_execution_events"].count_documents({}) == 1
        assert (
            await persistence.execution_events.list_for_interactions(
                session_id, user_id, [interaction_id]
            )
        )[0].status == ExecutionStatus.SUCCEEDED
        callback = observer.tool_callback()
        tool_id = uuid4()
        await callback.on_tool_start({"name": "lookup"}, "entrada", run_id=tool_id)
        await callback.on_tool_end("resultado", run_id=tool_id)
        document = await client[database_name]["ai_execution_events"].find_one(
            {"_id": str(tool_id)}
        )
        assert document["sanitized_input"] is None
        assert document["sanitized_output"] is None
        assert len(document["messages"]) == 2
        assert await client[database_name]["ai_execution_events"].count_documents({}) == 2
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_email_preparation_can_be_refreshed_without_changing_id():
    mongo_uri = os.getenv("MAIN_API_MONGO_TEST_URI")
    if not mongo_uri:
        pytest.skip("defina MAIN_API_MONGO_TEST_URI para executar a integração Mongo")
    client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    database_name = f"argos_email_workflow_test_{uuid4().hex}"
    try:
        repository = MongoEmailPreparationRepository(client[database_name]["ai_email_workflows"])
        await repository.ensure_indexes()
        now = datetime.now(UTC)
        user_id, session_id, interaction_id, artifact_id = (uuid4() for _ in range(4))
        original = EmailPreparation(
            reference=uuid4(),
            principal_user_id=user_id,
            session_id=session_id,
            interaction_id=interaction_id,
            report_artifact_ref=artifact_id,
            request_fingerprint="sha256:test",
            prepared_report=PreparedReport(
                report_id=uuid4(),
                status=PreparedReportStatus.PREPARED,
                requested_by=user_id,
                recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@test"),),
                subject="Relatório",
                body="Resumo",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            created_at=now,
        )
        await repository.save(original)
        expired = replace(
            original,
            prepared_report=original.prepared_report.model_copy(
                update={"expires_at": now - timedelta(seconds=1)}
            ),
        )
        await repository.save(expired)
        matched = await repository.find_matching(
            principal_user_id=user_id,
            session_id=session_id,
            report_artifact_ref=artifact_id,
            request_fingerprint=original.request_fingerprint,
        )
        assert matched is not None
        refreshed = replace(
            original,
            interaction_id=uuid4(),
            prepared_report=original.prepared_report.model_copy(
                update={"report_id": uuid4(), "created_at": now + timedelta(seconds=1)}
            ),
            created_at=now + timedelta(seconds=1),
        )
        await repository.save(refreshed)
        stored = await repository.get_by_reference(
            reference=original.reference,
            principal_user_id=user_id,
            session_id=session_id,
        )
        assert stored is not None
        assert stored.reference == original.reference
        assert stored.interaction_id == refreshed.interaction_id
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mongo_persistence_survives_client_restart_and_enforces_scope():
    mongo_uri = os.getenv("MAIN_API_MONGO_TEST_URI")
    if not mongo_uri:
        pytest.skip("defina MAIN_API_MONGO_TEST_URI para executar a integração Mongo")

    database_name = f"argos_main_api_test_{uuid4().hex}"
    owner_id = uuid4()
    other_user_id = uuid4()
    session_id = uuid4()
    second_farm_id = uuid4()
    interaction_id = uuid4()
    first_worker_id = uuid4()
    second_worker_id = uuid4()
    session_record = SessionRecord(
        session_id=session_id,
        user_id=owner_id,
        farm_ids=(uuid4(), second_farm_id),
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )

    client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    try:
        persistence = MongoPersistence(client[database_name])
        await persistence.ensure_indexes()
        await persistence.sessions.create(session_record)

        leases = await asyncio.gather(
            persistence.sessions.acquire_lease(
                session_id,
                owner_id,
                owner_id=first_worker_id,
                now=UTC_NOW,
                expires_at=UTC_NOW + timedelta(seconds=120),
            ),
            persistence.sessions.acquire_lease(
                session_id,
                owner_id,
                owner_id=second_worker_id,
                now=UTC_NOW,
                expires_at=UTC_NOW + timedelta(seconds=120),
            ),
        )
        lease = next((item for item in leases if item is not None), None)
        assert lease is not None
        assert sum(item is not None for item in leases) == 1

        interaction = InteractionRecord(
            interaction_id=interaction_id,
            session_id=session_id,
            user_id=owner_id,
            deduplication_key=str(uuid4()),
            kind=InteractionKind.MESSAGE,
            sanitized_input="Consultar dados do período",
            input_hash="sha256:integration-test",
            status=InteractionStatus.ACCEPTED,
            created_at=UTC_NOW,
            updated_at=UTC_NOW,
        )
        user_message = ConversationMessage(
            message_id=uuid4(),
            session_id=session_id,
            interaction_id=interaction_id,
            sequence=1,
            role=MessageRole.USER,
            kind=MessageKind.USER_INPUT,
            sanitized_content="Consultar dados do período",
            created_at=UTC_NOW,
            user_message_number=1,
        )
        accepted = await persistence.session_work.accept_user_interaction(
            interaction, user_message, lease, now=UTC_NOW
        )
        assert accepted is not None
        assert accepted.sequence == 1
        assert accepted.user_message_number == 1

        completed = replace(
            interaction,
            status=InteractionStatus.COMPLETED,
            updated_at=UTC_NOW + timedelta(seconds=1),
            result_json='{"response":{"kind":"answer","text":"Dados consultados"}}',
            version=2,
        )
        assistant_message = ConversationMessage(
            message_id=uuid4(),
            session_id=session_id,
            interaction_id=interaction_id,
            sequence=2,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.ASSISTANT_ANSWER,
            sanitized_content="Dados consultados",
            created_at=UTC_NOW + timedelta(seconds=1),
        )
        finalized = await persistence.session_work.finalize_interaction(
            completed,
            assistant_message,
            lease,
            expected_version=1,
            now=UTC_NOW + timedelta(seconds=1),
        )
        assert finalized is not None
        assert finalized.sequence == 2

        stored_interaction, created = await persistence.interactions.create_or_get(interaction)
        assert not created
        assert stored_interaction.status == InteractionStatus.COMPLETED

        event_id = uuid4()
        event = ExecutionEvent(
            event_id=event_id,
            invocation_id=event_id,
            session_id=session_id,
            interaction_id=interaction_id,
            target=ExecutionTarget.AGENT,
            target_name="farm",
            status=ExecutionStatus.SUCCEEDED,
            created_at=UTC_NOW,
            sanitized_input="Consultar período",
            sanitized_output="Dados consultados",
            completed_at=UTC_NOW + timedelta(seconds=1),
        )
        await persistence.execution_events.append(event)

        summary = SessionSummary(
            session_id=session_id,
            revision=1,
            through_sequence=2,
            through_user_message_number=1,
            activity="Consultar dados de uma sessão",
            important_facts=(SummaryFact("Período consultado: janeiro"),),
            decisions=("Usar o período informado pelo usuário",),
            pending_activity=None,
            limitations=("Dados sujeitos ao escopo autorizado",),
            summary_text="Consulta de dados do período, concluída.",
            token_count=12,
            updated_at=UTC_NOW,
        )
        assert await persistence.session_summaries.save_if_current(summary, expected_revision=None)

        memory = UserMemory(
            user_id=owner_id,
            tone="acolhedor e simples",
            general_goal="Entender medicamentos e prevenir novas perdas",
            tone_source_session_id=session_id,
            tone_source_interaction_id=interaction_id,
            goal_source_session_id=session_id,
            goal_source_interaction_id=interaction_id,
            revision=1,
            updated_at=UTC_NOW,
            tone_source_excerpt="Prefiro explicações acolhedoras e simples",
            goal_source_excerpt="Quero entender medicamentos e prevenir perdas",
        )
        assert await persistence.user_memory.save_if_current(memory, expected_revision=None)
        assert await persistence.user_memory.get(other_user_id) is None
        assert await persistence.sessions.get_owned(session_id, other_user_id) is None
    except Exception:
        await client.drop_database(database_name)
        raise
    finally:
        client.close()

    # Novo cliente simula reinicialização do processo contra o mesmo Mongo.
    reopened_client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    try:
        reopened = MongoPersistence(reopened_client[database_name])
        assert await reopened.sessions.get_owned(session_id, owner_id) is not None
        assert (await reopened.session_summaries.get_current(session_id)).summary_text == (
            "Consulta de dados do período, concluída."
        )
        assert (await reopened.user_memory.get(owner_id)).general_goal == (
            "Entender medicamentos e prevenir novas perdas"
        )
        events = await reopened.execution_events.list_for_interactions(
            session_id, owner_id, [interaction_id]
        )
        assert [item.target_name for item in events] == ["farm"]
        context_messages = await reopened.conversations.list_after_sequence(
            session_id,
            owner_id,
            after_sequence=0,
            through_sequence=2,
            limit=100,
        )
        assert [item.sequence for item in context_messages] == [1, 2]

        history = MessageHistoryService(
            sessions=reopened.sessions,
            conversations=reopened.conversations,
        )
        principal = ActorContext(
            user_id=owner_id,
            farm_ids=(session_record.farm_ids[0],),
        )
        page = await history.list_messages(principal, session_id, limit=1, cursor=None)
        assert page.items[0].sequence == 2
        assert page.items[0].redacted is True
        assert page.has_more and page.next_cursor
        older = await history.list_messages(principal, session_id, limit=1, cursor=page.next_cursor)
        assert [item.sequence for item in older.items] == [1]
        assert older.items[0].redacted is False

        with pytest.raises(ApplicationError) as unauthorized:
            await history.list_messages(
                ActorContext(
                    user_id=other_user_id,
                    farm_ids=tuple(session_record.farm_ids),
                ),
                session_id,
                limit=10,
                cursor=None,
            )
        assert unauthorized.value.code == ApplicationErrorCode.NOT_FOUND
    finally:
        await reopened_client.drop_database(database_name)
        reopened_client.close()
