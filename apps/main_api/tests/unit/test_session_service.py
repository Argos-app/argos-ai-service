"""Fluxos de sessão, deduplicação, contador e lease do serviço de aplicação."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.agent_schemas.inputs import QuestionFormattingRequest, QuestionOption
from app.application.contracts.interactions import (
    AnswerCommand,
    ChoiceAnswer,
    ConfirmationAnswer,
    InteractionOutcome,
    MessageCommand,
    QuestionOptionResult,
    QuestionResult,
    TextAnswer,
)
from app.application.errors import ApplicationError, ApplicationErrorCode
from app.application.identity import ActorContext
from app.application.session_service import SessionApplicationService, _response_from_state
from app.domain.email_preparations import (
    EmailPreparation,
    PendingRecipientOption,
    PendingRecipientResolution,
)
from app.domain.memory import (
    MemoryFieldAction,
    SessionSummary,
    UserMemoryChange,
    UserMemoryFieldChange,
)
from app.domain.sessions import (
    ConversationMessage,
    InteractionKind,
    InteractionRecord,
    InteractionStatus,
    ProcessingLease,
    SessionRecord,
)

from packages.contracts import (
    PreparedReport,
    PreparedReportStatus,
    RecipientRef,
    RecipientSelector,
)
from packages.observability import correlation_key


def _utcnow() -> datetime:
    return datetime.now(UTC)


def test_response_marks_tool_or_agent_failures_as_degraded():
    outcome = _response_from_state(
        session_id=uuid4(),
        interaction_id=uuid4(),
        public_request_id=uuid4(),
        state={"final_answer": "A consulta falhou.", "errors": ["farm_failed"]},
        now=_utcnow(),
    )

    assert outcome.status == "degraded"
    assert outcome.degradation_reasons == ("farm_failed",)


def test_response_marks_successful_graph_results_as_completed():
    outcome = _response_from_state(
        session_id=uuid4(),
        interaction_id=uuid4(),
        public_request_id=uuid4(),
        state={"final_answer": "Consulta concluída.", "errors": []},
        now=_utcnow(),
    )

    assert outcome.status == "completed"


def test_general_single_choice_is_exposed_with_server_assigned_option_ids():
    question_id = uuid4()
    options = (
        QuestionOption(option_id=str(uuid4()), label="Aplicações"),
        QuestionOption(option_id=str(uuid4()), label="Períodos de carência"),
    )
    request = QuestionFormattingRequest(
        reason_code="MISSING_FIELDS",
        input_type="single_choice",
        question_goal="Qual assunto você quer consultar?",
        options=options,
    )

    outcome = _response_from_state(
        session_id=uuid4(),
        interaction_id=uuid4(),
        public_request_id=uuid4(),
        state={
            "clarification_request": request,
            "clarification_question": "Você quer consultar aplicações ou carências?",
            "question_id": str(question_id),
            "errors": [],
        },
        now=_utcnow(),
    )

    assert outcome.response.kind == "question"
    assert outcome.response.input_type == "single_choice"
    assert [option.label for option in outcome.response.options] == [
        "Aplicações",
        "Períodos de carência",
    ]
    assert [option.option_id for option in outcome.response.options] == [
        option.option_id for option in options
    ]


def test_response_hides_executor_report_artifact_reference():
    reference = str(uuid4())
    outcome = _response_from_state(
        session_id=uuid4(),
        interaction_id=uuid4(),
        public_request_id=uuid4(),
        state={
            "final_answer": f"O relatório foi preparado. Referência: {reference}",
            "report_artifact_refs": {"farm-task": (reference,)},
        },
        now=_utcnow(),
    )

    assert outcome.response.kind == "answer"
    assert reference not in outcome.response.text
    assert "[referência interna]" in outcome.response.text


class FakeSessionRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, SessionRecord] = {}
        self.fencing_tokens: dict[UUID, int] = {}

    async def create(self, session: SessionRecord) -> None:
        self.records[session.session_id] = session

    async def get_owned(self, session_id: UUID, user_id: UUID) -> SessionRecord | None:
        record = self.records.get(session_id)
        return record if record and record.user_id == user_id else None

    async def list_owned(self, user_id: UUID) -> list[SessionRecord]:
        return sorted(
            (record for record in self.records.values() if record.user_id == user_id),
            key=lambda record: record.created_at,
            reverse=True,
        )

    async def acquire_lease(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ProcessingLease | None:
        record = await self.get_owned(session_id, user_id)
        if record is None:
            return None
        if record.processing_lease and record.processing_lease.expires_at > now:
            return None
        token = self.fencing_tokens.get(session_id, 0) + 1
        self.fencing_tokens[session_id] = token
        lease = ProcessingLease(owner_id, token, now, expires_at)
        self.records[session_id] = replace(
            record, processing_lease=lease, updated_at=now, version=record.version + 1
        )
        return lease

    async def renew_lease(
        self,
        session_id: UUID,
        lease: ProcessingLease,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        record = self.records.get(session_id)
        if record is None or record.processing_lease != lease:
            return False
        renewed = replace(lease, expires_at=expires_at)
        self.records[session_id] = replace(
            record, processing_lease=renewed, updated_at=now, version=record.version + 1
        )
        return True

    async def release_lease(self, session_id: UUID, lease: ProcessingLease) -> bool:
        record = self.records.get(session_id)
        if record is None or record.processing_lease != lease:
            return False
        now = _utcnow()
        self.records[session_id] = replace(
            record, processing_lease=None, updated_at=now, version=record.version + 1
        )
        return True


class FakeInteractionRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, InteractionRecord] = {}

    async def find_by_deduplication_key(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        kind: InteractionKind,
        deduplication_key: str,
    ) -> InteractionRecord | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.session_id == session_id
                and record.user_id == user_id
                and record.kind == kind
                and record.deduplication_key == deduplication_key
            ),
            None,
        )

    async def get_owned(self, interaction_id: UUID, user_id: UUID) -> InteractionRecord | None:
        record = self.records.get(interaction_id)
        return record if record and record.user_id == user_id else None

    async def save_result(self, interaction: InteractionRecord, *, expected_version: int) -> bool:
        current = self.records.get(interaction.interaction_id)
        if current is None or current.version != expected_version:
            return False
        self.records[interaction.interaction_id] = interaction
        return True


class FakeConversationRepository:
    def __init__(self) -> None:
        self.messages: list[ConversationMessage] = []

    async def append(self, message: ConversationMessage) -> None:
        self.messages.append(message)

    async def list_before_sequence(self, *args, **kwargs) -> tuple[ConversationMessage, ...]:
        return ()

    async def list_after_sequence(
        self,
        session_id,
        user_id,
        *,
        after_sequence,
        through_sequence,
        limit,
    ) -> tuple[ConversationMessage, ...]:
        del user_id
        return tuple(
            message
            for message in sorted(self.messages, key=lambda item: item.sequence)
            if message.session_id == session_id
            and after_sequence < message.sequence <= through_sequence
        )[:limit]


class FakeSessionWorkRepository:
    def __init__(self, sessions, interactions, conversations) -> None:
        self.sessions = sessions
        self.interactions = interactions
        self.conversations = conversations

    async def accept_user_interaction(self, interaction, message, lease, *, now):
        session = self.sessions.records[interaction.session_id]
        if session.processing_lease != lease:
            return None
        assigned = replace(
            message,
            sequence=session.next_message_sequence,
            user_message_number=session.user_message_count + 1,
        )
        self.sessions.records[session.session_id] = replace(
            session,
            next_message_sequence=session.next_message_sequence + 1,
            user_message_count=session.user_message_count + 1,
            updated_at=now,
            version=session.version + 1,
        )
        self.interactions.records[interaction.interaction_id] = interaction
        self.conversations.messages.append(assigned)
        return assigned

    async def finalize_interaction(self, interaction, message, lease, *, expected_version, now):
        session = self.sessions.records[interaction.session_id]
        current = self.interactions.records[interaction.interaction_id]
        if session.processing_lease != lease or current.version != expected_version:
            return None
        assigned = replace(message, sequence=session.next_message_sequence)
        self.sessions.records[session.session_id] = replace(
            session,
            next_message_sequence=session.next_message_sequence + 1,
            updated_at=now,
            version=session.version + 1,
        )
        self.interactions.records[interaction.interaction_id] = interaction
        self.conversations.messages.append(assigned)
        return assigned


class FakeGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.invocations = []
        self.state_configs = []
        self.question_id = str(uuid4())
        self.state_values = {"question_id": self.question_id}
        self.updates = []

    async def ainvoke(self, state, *, config):
        self.calls += 1
        self.invocations.append((state, config))
        if state.get("input_kind") == "answer":
            return {"final_answer": "Esclarecimento recebido."}
        return {
            "clarification_request": QuestionFormattingRequest(
                reason_code="MISSING_PERIOD",
                input_type="free_text",
                question_goal="Informe o período.",
            ),
            "clarification_question": "Qual período devo consultar?",
            "question_id": self.question_id,
        }

    async def aget_state(self, config):
        self.state_configs.append(config)
        return SimpleNamespace(values=dict(self.state_values))

    async def aupdate_state(self, config, values):
        self.state_configs.append(config)
        self.updates.append(values)
        self.state_values.update(values)
        self.question_id = str(values.get("question_id", self.question_id))


class FakeRecipientQuestionGraph(FakeGraph):
    def __init__(self, question_id, selectors):
        super().__init__()
        self.question_id = str(question_id)
        self.selectors = selectors

    async def aget_state(self, config):
        self.state_configs.append(config)
        return SimpleNamespace(
            values={
                **self.state_values,
                "question_id": self.question_id,
                "recipient_selectors": self.selectors,
            }
        )


class FakePendingRecipientRepository:
    def __init__(self, resolution):
        self.resolution = resolution

    async def get_pending_recipient_resolution(self, **scope):
        current = self.resolution
        matches = (
            current.principal_user_id == scope["principal_user_id"]
            and current.session_id == scope["session_id"]
            and current.interaction_id == scope["interaction_id"]
            and (scope.get("question_id") is None or current.question_id == scope["question_id"])
        )
        return current if matches else None


class FakePreparedEmailRepository:
    def __init__(self, preparations=()):
        self.preparations = tuple(preparations)

    async def list_prepared_for_session(self, *, principal_user_id, session_id, limit=20):
        return tuple(
            item
            for item in self.preparations[:limit]
            if item.principal_user_id == principal_user_id and item.session_id == session_id
        )


class FakeSendConfirmationGraph(FakeGraph):
    async def ainvoke(self, state, *, config):
        self.calls += 1
        self.invocations.append((state, config))
        if state.get("input_kind") == "message":
            task = SimpleNamespace(
                task_id="secretary-task",
                agent=SimpleNamespace(value="secretary"),
            )
            self.state_values.update(
                {
                    "send_intent_requested": state.get("send_intent_requested"),
                    "send_intent_source_interaction_id": state.get(
                        "send_intent_source_interaction_id"
                    ),
                    "recipient_selectors": state.get("recipient_selectors", {}),
                    "ordered_tasks": (task,),
                }
            )
            return {"final_answer": "Preparação pronta.", "ordered_tasks": (task,)}
        return {"final_answer": "Envio autorizado e concluído."}


def _principal() -> ActorContext:
    return ActorContext(user_id=uuid4(), farm_ids=(uuid4(),))


def _prepared_email(principal_id: UUID, session_id: UUID) -> EmailPreparation:
    now = _utcnow()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=principal_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório de teste",
        body="Conteúdo do relatório.",
        created_at=now,
        expires_at=now.replace(year=now.year + 1),
    )
    return EmailPreparation(
        reference=uuid4(),
        principal_user_id=principal_id,
        session_id=session_id,
        interaction_id=uuid4(),
        report_artifact_ref=uuid4(),
        request_fingerprint="request-hash",
        prepared_report=prepared,
        created_at=now,
    )


def _service(graph=None, **options):
    sessions = FakeSessionRepository()
    interactions = FakeInteractionRepository()
    conversations = FakeConversationRepository()
    work = FakeSessionWorkRepository(sessions, interactions, conversations)
    service = SessionApplicationService(
        sessions=sessions,
        interactions=interactions,
        conversations=conversations,
        session_work=work,
        graph_provider=lambda: graph,
        renew_seconds=60,
        **options,
    )
    return service, sessions, interactions, conversations


@pytest.mark.asyncio
async def test_async_submission_persists_status_and_returns_structured_result():
    graph = FakeGraph()
    scheduled = []
    service, sessions, interactions, _ = _service(
        graph,
        background_task_scheduler=scheduled.append,
    )
    principal = _principal()
    session = await service.create_session(principal)
    request = MessageCommand(
        kind="message",
        client_message_id=uuid4(),
        message="Consulte a fazenda",
    )

    operation_id = await service.submit_interaction(principal, session.session_id, request)

    assert graph.calls == 0
    assert interactions.records[operation_id].status == InteractionStatus.RUNNING
    assert sessions.records[session.session_id].processing_lease is not None
    assert len(scheduled) == 1
    assert await service.submit_interaction(principal, session.session_id, request) == operation_id
    assert len(scheduled) == 1

    await scheduled.pop()
    await scheduled.pop()
    operation = await service.get_interaction_operation(principal, session.session_id, operation_id)

    assert operation.status == "completed"
    assert operation.result is not None
    assert operation.result.response.kind == "question"
    assert operation.result.created_at == interactions.records[operation_id].updated_at
    assert sessions.records[session.session_id].processing_lease is None


@pytest.mark.asyncio
async def test_create_and_list_sessions_are_scoped_to_principal():
    service, sessions, _, _ = _service()
    principal = _principal()
    created = await service.create_session(principal)

    listed = await service.list_sessions(principal)
    other = await service.list_sessions(_principal())

    assert created.session_id == listed[0].session_id
    assert tuple(created.farm_ids) == tuple(principal.farm_ids)
    assert other == []
    assert sessions.records[created.session_id].user_id == principal.user_id


@pytest.mark.asyncio
async def test_message_is_sanitized_and_replay_counts_once():
    graph = FakeGraph()
    service, sessions, _, conversations = _service(graph)
    principal = _principal()
    session = await service.create_session(principal)
    request = MessageCommand(
        kind="message",
        client_message_id=uuid4(),
        message="Preciso consultar usuario@example.invalid",
    )

    first = await service.create_interaction(principal, session.session_id, request)
    replay = await service.create_interaction(principal, session.session_id, request)

    record = sessions.records[session.session_id]
    assert first == replay
    assert record.user_message_count == 1
    assert record.next_message_sequence == 3
    assert graph.calls == 1
    assert len(conversations.messages) == 2
    assert conversations.messages[0].sanitized_content.endswith("usuario@example.invalid")
    assert all(message.sequence == index for index, message in enumerate(conversations.messages, 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["context", "graph"])
@pytest.mark.parametrize("renewal", ["refused", "error", "timeout"])
async def test_lease_loss_cancels_processing_before_finalization(
    monkeypatch, caplog, phase, renewal
):
    graph = FakeGraph()
    service, sessions, interactions, conversations = _service(graph)
    service._renew_seconds = 0.001
    principal = _principal()
    session = await service.create_session(principal)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked_phase(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def renew(*args, **kwargs):
        await started.wait()
        if renewal == "error":
            raise RuntimeError("database unavailable")
        if renewal == "timeout":
            raise TimeoutError("database timeout")
        return False

    target, method = (
        (service, "_build_interaction_context") if phase == "context" else (graph, "ainvoke")
    )
    monkeypatch.setattr(target, method, blocked_phase)
    monkeypatch.setattr(sessions, "renew_lease", renew)
    finalize = AsyncMock()
    release = AsyncMock(wraps=sessions.release_lease)
    monkeypatch.setattr(service, "_finalize_interaction", finalize)
    monkeypatch.setattr(sessions, "release_lease", release)

    with pytest.raises(ApplicationError) as caught:
        await asyncio.wait_for(
            service.create_interaction(
                principal,
                session.session_id,
                MessageCommand(kind="message", client_message_id=uuid4(), message="Consultar lote"),
            ),
            timeout=5,
        )

    assert caught.value.code == ApplicationErrorCode.CONFLICT
    assert stopped.is_set()
    finalize.assert_not_awaited()
    release.assert_awaited_once()
    assert sessions.records[session.session_id].processing_lease is None
    assert len(conversations.messages) == 1
    assert next(iter(interactions.records.values())).status == InteractionStatus.RUNNING
    if renewal != "refused":
        assert "session lease renewal failed" in caplog.text


@pytest.mark.asyncio
async def test_request_cancellation_stops_processing_and_releases_lease(monkeypatch):
    service, sessions, _, _ = _service(FakeGraph())
    principal = _principal()
    session = await service.create_session(principal)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked_context(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(service, "_build_interaction_context", blocked_context)
    task = asyncio.create_task(
        service.create_interaction(
            principal,
            session.session_id,
            MessageCommand(kind="message", client_message_id=uuid4(), message="Consultar lote"),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert stopped.is_set()
    assert sessions.records[session.session_id].processing_lease is None


@pytest.mark.asyncio
async def test_partial_farm_revocation_blocks_replaying_persisted_interaction():
    graph = FakeGraph()
    service, sessions, _, _ = _service(graph)
    farm_ids = (uuid4(), uuid4())
    principal = ActorContext(user_id=uuid4(), farm_ids=farm_ids)
    session = await service.create_session(principal)
    request = MessageCommand(
        kind="message",
        client_message_id=uuid4(),
        message="Consulte os dados da fazenda.",
    )
    first = await service.create_interaction(principal, session.session_id, request)
    partially_authorized = ActorContext(user_id=principal.user_id, farm_ids=(farm_ids[0],))

    with pytest.raises(ApplicationError) as denied:
        await service.create_interaction(partially_authorized, session.session_id, request)

    assert denied.value.code == ApplicationErrorCode.FORBIDDEN
    assert "nova sessão" in denied.value.detail
    assert graph.calls == 1
    assert sessions.records[session.session_id].user_message_count == 1
    assert first.session_id == session.session_id


@pytest.mark.asyncio
async def test_partial_farm_revocation_blocks_resuming_a_persisted_question():
    graph = FakeGraph()
    service, _, _, _ = _service(graph)
    farm_ids = (uuid4(), uuid4())
    principal = ActorContext(user_id=uuid4(), farm_ids=farm_ids)
    session = await service.create_session(principal)
    initial = await service.create_interaction(
        principal,
        session.session_id,
        MessageCommand(
            kind="message",
            client_message_id=uuid4(),
            message="Consulte os dados da fazenda.",
        ),
    )
    answer = AnswerCommand(
        kind="answer",
        interaction_id=initial.interaction_id,
        question_id=initial.response.question_id,
        answer=TextAnswer(kind="text", text="Últimos 30 dias"),
    )
    partially_authorized = ActorContext(user_id=principal.user_id, farm_ids=(farm_ids[0],))

    with pytest.raises(ApplicationError) as denied:
        await service.create_interaction(partially_authorized, session.session_id, answer)

    assert denied.value.code == ApplicationErrorCode.FORBIDDEN
    assert graph.calls == 1
    assert graph.state_configs == []


@pytest.mark.asyncio
async def test_langsmith_trace_metadata_contains_interaction_correlation_ids():
    graph = FakeGraph()
    callback = object()
    service, _, _, _ = _service(graph, langsmith_callback=callback)
    principal = _principal()
    session = await service.create_session(principal)
    request = MessageCommand(
        kind="message",
        client_message_id=uuid4(),
        message="Consulte os dados da fazenda.",
    )

    outcome = await service.create_interaction(principal, session.session_id, request)
    trace_config = graph.invocations[0][1]

    assert trace_config["metadata"] == {
        "argos_operation": "session.interaction",
        "argos_correlation_id": str(outcome.public_request_id),
        "argos_correlation_key": correlation_key(outcome.public_request_id),
        "argos_session_id": str(outcome.session_id),
        "argos_interaction_id": str(outcome.interaction_id),
    }
    assert trace_config["callbacks"] == [callback]


@pytest.mark.asyncio
async def test_degraded_result_is_persisted_as_terminal_degraded_status():
    class DegradedGraph(FakeGraph):
        async def ainvoke(self, state, *, config):
            del state, config
            return {"final_answer": "A consulta foi degradada.", "errors": ["farm_failed"]}

    service, _, interactions, _ = _service(DegradedGraph())
    principal = _principal()
    session = await service.create_session(principal)
    request = MessageCommand(
        kind="message",
        client_message_id=uuid4(),
        message="Consulte os dados da fazenda.",
    )

    outcome = await service.create_interaction(principal, session.session_id, request)
    stored = interactions.records[outcome.interaction_id]

    assert outcome.status == "degraded"
    assert outcome.degradation_reasons == ("farm_failed",)
    assert stored.status == InteractionStatus.DEGRADED
    assert stored.result_json is not None


@pytest.mark.asyncio
async def test_degraded_interaction_with_pending_question_can_be_resumed():
    class DegradedQuestionGraph(FakeGraph):
        async def ainvoke(self, state, *, config):
            if state.get("input_kind") == "answer":
                self.calls += 1
                self.invocations.append((state, config))
                return {"final_answer": "Período recebido."}
            result = await super().ainvoke(state, config=config)
            result["errors"] = ["router_invalid_output"]
            return result

    graph = DegradedQuestionGraph()
    service, _, _, _ = _service(graph)
    principal = _principal()
    session = await service.create_session(principal)
    request = MessageCommand(
        kind="message", client_message_id=uuid4(), message="Consulte as aplicações."
    )

    question = await service.create_interaction(principal, session.session_id, request)
    assert question.status == "degraded"
    assert question.response.kind == "question"

    answer = AnswerCommand(
        kind="answer",
        interaction_id=question.interaction_id,
        question_id=question.response.question_id,
        answer=TextAnswer(kind="text", text="Últimos 30 dias"),
    )
    result = await service.create_interaction(principal, session.session_id, answer)

    assert result.status == "completed"
    assert result.response.kind == "answer"
    assert result.response.text == "Período recebido."
    assert graph.calls == 2


@pytest.mark.asyncio
async def test_answer_is_counted_once_and_conflicting_replay_is_rejected():
    graph = FakeGraph()
    service, sessions, _, conversations = _service(graph)
    principal = _principal()
    session = await service.create_session(principal)
    message = MessageCommand(
        kind="message", client_message_id=uuid4(), message="Consulte o relatório"
    )
    question = await service.create_interaction(principal, session.session_id, message)
    answer = AnswerCommand(
        kind="answer",
        interaction_id=question.interaction_id,
        question_id=UUID(graph.question_id),
        answer=TextAnswer(kind="text", text="Últimos 30 dias"),
    )

    result = await service.create_interaction(principal, session.session_id, answer)
    replay = await service.create_interaction(principal, session.session_id, answer)

    assert result == replay
    assert sessions.records[session.session_id].user_message_count == 2
    assert sessions.records[session.session_id].next_message_sequence == 5
    assert len(conversations.messages) == 4
    assert graph.state_configs[0]["configurable"] == {"thread_id": str(session.session_id)}
    answer_configurable = graph.invocations[1][1]["configurable"]
    assert answer_configurable["thread_id"] == str(session.session_id)
    assert "checkpoint_ns" not in answer_configurable
    conflicting = answer.model_copy(update={"answer": TextAnswer(kind="text", text="Setembro")})
    with pytest.raises(ApplicationError) as error:
        await service.create_interaction(principal, session.session_id, conflicting)
    assert error.value.code == ApplicationErrorCode.CONFLICT
    assert graph.calls == 2


@pytest.mark.asyncio
async def test_ambiguous_recipient_choice_resolves_to_protected_email_selector():
    principal = _principal()
    question_interaction_id = uuid4()
    question_id = uuid4()
    selected_option_id = str(uuid4())
    now = _utcnow()
    graph = FakeRecipientQuestionGraph(
        question_id,
        {
            "[DESTINATARIO_1]": {"kind": "name", "value": "Alex"},
            "[DESTINATARIO_2]": {"kind": "name", "value": "Bruno"},
        },
    )
    repository = FakePendingRecipientRepository(None)
    service, sessions, interactions, _ = _service(graph, email_preparations=repository)
    session = await service.create_session(principal)
    selector = RecipientSelector(kind="email", value="alex.silva@example.com")
    pending = PendingRecipientResolution(
        question_id=question_id,
        principal_user_id=principal.user_id,
        session_id=session.session_id,
        interaction_id=question_interaction_id,
        report_artifact_ref=uuid4(),
        input_type="single_choice",
        reason_code="RECIPIENT_AMBIGUOUS",
        question_goal="Escolha o destinatário correto.",
        unresolved_selector=RecipientSelector(kind="name", value="Alex"),
        options=(
            PendingRecipientOption(
                option_id=selected_option_id,
                label="Alex Silva (a***@example.com)",
                selector=selector,
            ),
        ),
        expires_at=now.replace(year=now.year + 1),
    )
    repository.resolution = pending
    question = QuestionResult(
        kind="question",
        question_id=question_id,
        input_type="single_choice",
        prompt="Qual Alex?",
        reason_code="RECIPIENT_AMBIGUOUS",
        options=[
            QuestionOptionResult(
                option_id=selected_option_id,
                label="Alex Silva (a***@example.com)",
            )
        ],
        expires_at=now.replace(year=now.year + 1),
    )
    interactions.records[question_interaction_id] = InteractionRecord(
        interaction_id=question_interaction_id,
        session_id=session.session_id,
        user_id=principal.user_id,
        deduplication_key="request",
        kind=InteractionKind.MESSAGE,
        sanitized_input="Prepare relatório para Alex",
        input_hash="hash",
        status=InteractionStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        result_json=InteractionOutcome(
            session_id=session.session_id,
            interaction_id=question_interaction_id,
            public_request_id=uuid4(),
            response=question,
        ).model_dump_json(),
    )
    answer = AnswerCommand(
        kind="answer",
        interaction_id=question_interaction_id,
        question_id=question_id,
        answer=ChoiceAnswer(kind="choice", option_id=selected_option_id),
    )

    result = await service._validate_answer(
        sessions.records[session.session_id],
        answer,
        graph,
        new_recipient_selectors={},
    )

    graph_answer, selectors = result[:2]
    assert graph_answer == "[DESTINATARIO_3]"
    assert selectors["[DESTINATARIO_3]"]["value"] == "alex.silva@example.com"
    assert "[DESTINATARIO_1]" not in selectors
    assert selectors["[DESTINATARIO_2]"]["value"] == "Bruno"


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [True, False])
async def test_email_confirmation_answer_is_bound_to_saved_report(confirm):
    principal = _principal()
    graph = FakeGraph()
    repository = FakePreparedEmailRepository()
    service, sessions, interactions, _ = _service(graph, email_preparations=repository)
    session = await service.create_session(principal)
    preparation = _prepared_email(principal.user_id, session.session_id)
    repository.preparations = (preparation,)
    source_interaction_id = uuid4()
    question_id = uuid4()
    now = _utcnow()
    question = QuestionResult(
        kind="question",
        question_id=question_id,
        input_type="confirmation",
        prompt="Confirma o envio do relatório?",
        reason_code="EMAIL_SEND_CONFIRMATION",
        expires_at=now.replace(year=now.year + 1),
    )
    interactions.records[source_interaction_id] = InteractionRecord(
        interaction_id=source_interaction_id,
        session_id=session.session_id,
        user_id=principal.user_id,
        deduplication_key="confirmation",
        kind=InteractionKind.MESSAGE,
        sanitized_input="Enviar o relatório",
        input_hash="confirmation-hash",
        status=InteractionStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        result_json=InteractionOutcome(
            session_id=session.session_id,
            interaction_id=source_interaction_id,
            public_request_id=uuid4(),
            response=question,
        ).model_dump_json(),
    )
    graph.question_id = str(question_id)
    graph.state_values.update(
        {
            "question_id": str(question_id),
            "send_intent_requested": True,
            "send_intent_source_interaction_id": str(source_interaction_id),
            "recipient_selectors": {"recipient-1": {"kind": "email", "value": "ana@example.com"}},
            "email_send_confirmation_report_id": str(preparation.prepared_report.report_id),
        }
    )
    answer = AnswerCommand(
        kind="answer",
        interaction_id=source_interaction_id,
        question_id=question_id,
        answer=ConfirmationAnswer(kind="confirmation", confirm=confirm),
    )

    result = await service._validate_answer(
        sessions.records[session.session_id],
        answer,
        graph,
        new_recipient_selectors={},
    )

    assert result[4] is True
    if confirm:
        assert result[5] == question_id
        assert result[6] == preparation.prepared_report.report_id
        assert result[7] == question.expires_at
    else:
        assert result[5:] == (None, None, None)


@pytest.mark.asyncio
async def test_declining_email_confirmation_clears_pending_checkpoint():
    principal = _principal()
    graph = FakeGraph()
    service, sessions, interactions, _ = _service(
        graph,
        email_preparations=FakePreparedEmailRepository(),
    )
    session = await service.create_session(principal)
    source_interaction_id = uuid4()
    question_id = uuid4()
    now = _utcnow()
    question = QuestionResult(
        kind="question",
        question_id=question_id,
        input_type="confirmation",
        prompt="Confirma o envio do relatório?",
        reason_code="EMAIL_SEND_CONFIRMATION",
        expires_at=now.replace(year=now.year + 1),
    )
    interactions.records[source_interaction_id] = InteractionRecord(
        interaction_id=source_interaction_id,
        session_id=session.session_id,
        user_id=principal.user_id,
        deduplication_key="confirmation",
        kind=InteractionKind.MESSAGE,
        sanitized_input="Enviar o relatório",
        input_hash="confirmation-hash",
        status=InteractionStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        result_json=InteractionOutcome(
            session_id=session.session_id,
            interaction_id=source_interaction_id,
            public_request_id=uuid4(),
            response=question,
        ).model_dump_json(),
    )
    graph.question_id = str(question_id)
    graph.state_values.update(
        {
            "question_id": str(question_id),
            "send_intent_requested": True,
            "send_intent_source_interaction_id": str(source_interaction_id),
            "email_send_confirmation_report_id": str(uuid4()),
        }
    )
    answer = AnswerCommand(
        kind="answer",
        interaction_id=source_interaction_id,
        question_id=question_id,
        answer=ConfirmationAnswer(kind="confirmation", confirm=False),
    )

    result = await service.create_interaction(principal, session.session_id, answer)

    assert result.response.kind == "answer"
    assert result.response.text == "Envio cancelado. Nenhum e-mail foi enviado."
    assert graph.calls == 0
    assert graph.state_values["question_id"] is None
    assert graph.state_values["send_intent_requested"] is False
    with pytest.raises(ApplicationError) as reused:
        await service.create_interaction(
            principal,
            session.session_id,
            answer.model_copy(
                update={"answer": ConfirmationAnswer(kind="confirmation", confirm=True)}
            ),
        )
    assert reused.value.code == ApplicationErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_explicit_send_intent_creates_persisted_confirmation_question():
    principal = _principal()
    graph = FakeSendConfirmationGraph()
    repository = FakePreparedEmailRepository()
    service, _, _, _ = _service(graph, email_preparations=repository)
    session = await service.create_session(principal)
    repository.preparations = (_prepared_email(principal.user_id, session.session_id),)
    accepted = SimpleNamespace(
        request=SimpleNamespace(
            send_intent_requested=True,
            confirmed_send_question_id=None,
            send_confirmation=False,
            recipient_selectors={},
        ),
        principal=principal,
        session=session,
        graph=graph,
        running=SimpleNamespace(interaction_id=uuid4()),
    )
    context = SimpleNamespace(graph_config={"configurable": {"thread_id": str(session.session_id)}})
    state = {
        "final_answer": "Relatório preparado.",
        "ordered_tasks": (
            SimpleNamespace(task_id="secretary-task", agent=SimpleNamespace(value="secretary")),
        ),
    }

    updated = await service._attach_send_confirmation(accepted, context, state)

    assert updated["clarification_request"].input_type == "confirmation"
    assert updated["clarification_request"].reason_code == "EMAIL_SEND_CONFIRMATION"
    assert "Ana <ana@example.com>" in updated["clarification_question"]
    assert "Relatório de teste" in updated["clarification_question"]
    assert updated["question_id"] == graph.question_id
    assert datetime.fromisoformat(updated["clarification_expires_at"]).microsecond % 1000 == 0
    assert graph.state_values["email_send_confirmation_report_id"] == str(
        repository.preparations[0].prepared_report.report_id
    )


class FakeSummaryRepository:
    def __init__(self):
        self.current = None
        self.saved = []

    async def get_current(self, session_id):
        return self.current if self.current and self.current.session_id == session_id else None

    async def save_if_current(self, summary, *, expected_revision):
        current_revision = self.current.revision if self.current else None
        if current_revision != expected_revision:
            return False
        self.current = summary
        self.saved.append(summary)
        return True


class FakeExecutionEvents:
    async def list_for_interactions(self, session_id, user_id, interaction_ids):
        del session_id, user_id, interaction_ids
        return ()


class FakeUserMemoryRepository:
    def __init__(self):
        self.memories = {}

    async def get(self, user_id):
        return self.memories.get(user_id)

    async def save_if_current(self, memory, *, expected_revision):
        current = self.memories.get(memory.user_id)
        if (current.revision if current else None) != expected_revision:
            return False
        self.memories[memory.user_id] = memory
        return True


class FakeUserMemoryExtractor:
    async def extract(self, *, user_id, session_id, sanitized_user_input, current):
        del user_id, current
        excerpt = "Prefiro linguagem simples."
        if excerpt not in sanitized_user_input:
            return UserMemoryChange()
        return UserMemoryChange(
            tone=UserMemoryFieldChange(
                action=MemoryFieldAction.SET,
                value="linguagem simples",
                source_excerpt=excerpt,
            )
        )


class FakeSummaryGenerator:
    def __init__(self):
        self.calls = []

    async def generate(
        self,
        *,
        session_id,
        previous,
        messages,
        events,
        through_user_message_number,
        max_tokens,
    ):
        self.calls.append((tuple(messages), tuple(events), through_user_message_number))
        return SessionSummary(
            session_id=session_id,
            revision=previous.revision + 1 if previous else 1,
            through_sequence=max(message.sequence for message in messages),
            through_user_message_number=through_user_message_number,
            activity="Continuar consulta.",
            important_facts=(),
            decisions=(),
            pending_activity=None,
            limitations=(),
            summary_text='{"activity":"Continuar consulta."}',
            token_count=5,
            updated_at=_utcnow(),
        )


@pytest.mark.asyncio
async def test_service_summarizes_only_after_the_fifteenth_completed_user_entry():
    graph = FakeGraph()
    summaries = FakeSummaryRepository()
    generator = FakeSummaryGenerator()
    service, _, _, _ = _service(
        graph,
        summaries=summaries,
        execution_events=FakeExecutionEvents(),
        summary_generator_provider=lambda: generator,
    )
    principal = _principal()
    session = await service.create_session(principal)

    for _ in range(15):
        await service.create_interaction(
            principal,
            session.session_id,
            MessageCommand(
                kind="message",
                client_message_id=uuid4(),
                message="Quero continuar a consulta.",
            ),
        )

    assert len(generator.calls) == 1
    assert generator.calls[0][2] == 15
    assert len(generator.calls[0][0]) == 30
    assert summaries.current.through_user_message_number == 15
    assert summaries.current.through_sequence == 30


@pytest.mark.asyncio
async def test_declared_memory_is_saved_and_used_in_the_same_interaction_context():
    graph = FakeGraph()
    memories = FakeUserMemoryRepository()
    service, _, _, _ = _service(
        graph,
        user_memory=memories,
        user_memory_extractor_provider=lambda: FakeUserMemoryExtractor(),
    )
    principal = _principal()
    session = await service.create_session(principal)

    await service.create_interaction(
        principal,
        session.session_id,
        MessageCommand(
            kind="message",
            client_message_id=uuid4(),
            message="Prefiro linguagem simples.",
        ),
    )

    memory = await memories.get(principal.user_id)
    assert memory is not None
    assert memory.tone == "linguagem simples"
    assert memory.tone_source_session_id == session.session_id
    assert memory.tone_source_excerpt == "Prefiro linguagem simples."
    assert graph.invocations[0][0]["session_context"].user_tone == "linguagem simples"
