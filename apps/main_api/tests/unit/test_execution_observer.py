from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.sessions import ExecutionEvent, ExecutionStatus, ExecutionTarget
from app.infrastructure.agents.execution_observer import ExecutionObserver
from app.ports.execution_event_repository import ExecutionEventWriteError


class FakeExecutionEventRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.events = []
        self.fail = fail

    async def append(self, event):
        if self.fail:
            raise OSError("database details must not escape")
        self.events.append(event)


def test_execution_event_requires_invocation_id_as_event_id():
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="event_id deve ser igual a invocation_id"):
        ExecutionEvent(
            event_id=uuid4(),
            invocation_id=uuid4(),
            session_id=uuid4(),
            interaction_id=uuid4(),
            target=ExecutionTarget.AGENT,
            target_name="farm",
            status=ExecutionStatus.STARTED,
            created_at=now,
        )


@pytest.mark.asyncio
async def test_agent_events_are_correlated_and_sanitized():
    repository = FakeExecutionEventRepository()
    observer = ExecutionObserver(repository, session_id=uuid4(), interaction_id=uuid4())

    invocation_id = await observer.agent_started(
        "farm",
        {"message": "Contato user@example.invalid", "api_key": "must-not-persist"},
    )
    await observer.agent_finished(
        invocation_id,
        "farm",
        output={"answer": "CPF 123.456.789-01", "access_token": "also-secret"},
    )

    started, succeeded = repository.events
    assert started.target == ExecutionTarget.AGENT
    assert started.status == ExecutionStatus.STARTED
    assert succeeded.status == ExecutionStatus.SUCCEEDED
    assert started.invocation_id == succeeded.invocation_id == invocation_id
    assert started.event_id == succeeded.event_id == invocation_id
    assert "user@example.invalid" in started.sanitized_input
    assert "must-not-persist" not in started.sanitized_input
    assert "123.456.789-01" not in succeeded.sanitized_output
    assert "also-secret" not in succeeded.sanitized_output


@pytest.mark.asyncio
async def test_tool_callback_persists_start_and_terminal_result():
    repository = FakeExecutionEventRepository()
    observer = ExecutionObserver(repository, session_id=uuid4(), interaction_id=uuid4())
    callback = observer.tool_callback()
    invocation_id = uuid4()

    await callback.on_tool_start(
        {"name": "lookup"},
        '{"api_key":"private","contact":"tool@example.invalid"}',
        run_id=invocation_id,
    )
    await callback.on_tool_end(
        "Resultado para user@example.invalid",
        run_id=invocation_id,
    )

    started, succeeded = repository.events
    assert started.target == ExecutionTarget.TOOL
    assert started.status == ExecutionStatus.STARTED
    assert succeeded.status == ExecutionStatus.SUCCEEDED
    assert started.invocation_id == succeeded.invocation_id == invocation_id
    assert started.messages[0].tool_call_id == str(invocation_id)
    assert "private" not in started.sanitized_input
    assert "tool@example.invalid" in started.sanitized_input
    assert "user@example.invalid" in succeeded.sanitized_output


@pytest.mark.asyncio
async def test_tool_failure_records_safe_code_without_exception_text():
    repository = FakeExecutionEventRepository()
    observer = ExecutionObserver(repository, session_id=uuid4(), interaction_id=uuid4())
    callback = observer.tool_callback()
    invocation_id = uuid4()

    await callback.on_tool_start({"name": "lookup"}, "{}", run_id=invocation_id)
    await callback.on_tool_error(
        RuntimeError("contains private service response"), run_id=invocation_id
    )

    failed = repository.events[-1]
    assert failed.status == ExecutionStatus.FAILED
    assert failed.safe_error_code == "tool_execution_failed"
    assert "private service response" not in repr(failed)


@pytest.mark.asyncio
async def test_event_write_failure_is_reported_as_safe_port_error():
    observer = ExecutionObserver(
        FakeExecutionEventRepository(fail=True),
        session_id=uuid4(),
        interaction_id=uuid4(),
    )

    with pytest.raises(ExecutionEventWriteError) as error:
        await observer.agent_started("farm", {"message": "consulta"})

    assert "database details" not in str(error.value)
