from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from argos_a2a_agent.adapters.inbound import a2a_server
from argos_a2a_agent.adapters.observability.recording import observe_adapter
from argos_a2a_agent.adapters.providers.http import _provider_usage

from packages.contracts.a2a import (
    CorpusRefreshStatus,
    DocumentAnswerResponse,
    DocumentAnswerStatus,
    DocumentSkill,
)
from packages.observability import (
    ObservationStatus,
    OperationKind,
    UsageAvailability,
    billing_with_fallback,
    provider_billing,
)
from packages.security.access import DocumentAccessContext


class Collector:
    def __init__(self) -> None:
        self.observations = []

    def emit(self, observation) -> None:
        self.observations.append(observation)


def run_immediate(coroutine):
    iterator = coroutine.__await__()
    while True:
        try:
            yielded = iterator.send(None)
        except StopIteration as completed:
            return completed.value
        if yielded is not None:
            raise AssertionError("fake de unidade não deveria suspender a corrotina")


def test_observed_adapter_records_timing_without_arguments_or_results() -> None:
    class Adapter:
        async def read(self, secret: str) -> dict[str, str]:
            return {"private": secret}

    collector = Collector()
    wrapped = observe_adapter(Adapter(), collector, operation_prefix="documents.source")

    result = run_immediate(wrapped.read("sensitive payload"))

    assert result == {"private": "sensitive payload"}
    assert [item.status for item in collector.observations] == [
        ObservationStatus.STARTED,
        ObservationStatus.SUCCEEDED,
    ]
    terminal = collector.observations[-1]
    assert terminal.operation_kind == OperationKind.STORAGE
    assert terminal.operation_name == "documents.source.read"
    assert terminal.ended_at is not None
    assert terminal.duration_ms is not None
    assert "sensitive payload" not in repr(terminal.model_dump())


def test_provider_usage_preserves_only_returned_numeric_categories() -> None:
    usage = _provider_usage(
        {
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "total_tokens": 17,
            "prompt_tokens_details": {"cached_tokens": 3, "cache_creation_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 1},
            "custom_tokens": 4,
            "sensitive": "do not persist",
        }
    )

    assert usage.availability == UsageAvailability.RETURNED
    assert usage.source == "provider"
    assert usage.input_tokens == 12
    assert usage.output_tokens == 5
    assert usage.total_tokens == 17
    assert usage.cache_read_tokens == 3
    assert usage.cache_write_tokens == 2
    assert usage.reasoning_tokens == 1
    assert usage.details == {"custom_tokens": 4}


def test_provider_billing_requires_explicit_currency_or_usd_field() -> None:
    billing = provider_billing({"usage": {"total_cost_usd": "0.0000000000125"}})
    assert billing is not None
    assert str(billing.reported_cost) == "1.25E-11"
    assert billing.currency == "USD"
    assert billing.cost_source == "provider"

    assert provider_billing({"usage": {"prompt_tokens": 12, "total_tokens": 12}}) is None
    assert provider_billing({"usage": {"cost": "0.00125"}}) is None


def test_a2a_provider_usage_aliases_are_used_for_configured_rate_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    monkeypatch.setenv("MODEL_OUTPUT_COST_PER_MILLION_TOKENS", "10")
    usage = _provider_usage({"input_tokens": 200, "output_tokens": 50})

    billing = billing_with_fallback(
        {}, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
    )

    assert billing is not None
    assert str(billing.reported_cost) == "0.0010"
    assert billing.cost_source == "configured_rate"


def test_completed_a2a_task_keeps_unavailable_result_distinct(monkeypatch) -> None:
    collector = Collector()
    request_id = uuid4()
    correlation_id = uuid4()
    parent_observation_id = uuid4()
    session_id = uuid4()
    interaction_id = uuid4()
    request = {
        "request_id": str(request_id),
        "question": "Pergunta de teste",
        "skill": DocumentSkill.DOCUMENTAL.value,
        "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }
    access = DocumentAccessContext(
        user_id=uuid4(),
        operations=frozenset({"documents.read"}),
        agent_id="informacoes",
        issued_at=datetime.now(UTC) - timedelta(seconds=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        token_id="token-test",
        request_id="request-test",
        correlation_id=correlation_id,
        parent_observation_id=parent_observation_id,
        session_id=session_id,
        interaction_id=interaction_id,
    )

    class FakeUpdater:
        def __init__(self, *_args) -> None:
            pass

        async def start_work(self) -> None:
            pass

        async def add_artifact(self, *_args, **_kwargs) -> None:
            pass

        async def complete(self) -> None:
            pass

    class Registry:
        async def execute(self, _request, _access) -> DocumentAnswerResponse:
            return DocumentAnswerResponse(
                request_id=request_id,
                status=DocumentAnswerStatus.UNAVAILABLE,
                limitations=("consulta indisponível",),
                refresh_status=CorpusRefreshStatus.UNAVAILABLE,
            )

    monkeypatch.setattr(a2a_server, "TaskUpdater", FakeUpdater)
    context = SimpleNamespace(
        task_id="task-test",
        context_id="context-test",
        current_task=object(),
        message=SimpleNamespace(
            parts=[
                SimpleNamespace(
                    text=json.dumps(request),
                    media_type="application/json",
                    WhichOneof=lambda _name: "text",
                )
            ]
        ),
        call_context=SimpleNamespace(state={a2a_server.ACCESS_CONTEXT_KEY: access}),
    )

    executor = a2a_server.DocumentA2AExecutor(Registry(), collector)
    run_immediate(executor.execute(context, object()))

    terminal = collector.observations[-1]
    assert terminal.status == ObservationStatus.SUCCEEDED
    assert terminal.protocol.task_state == "completed"
    assert terminal.result.code == DocumentAnswerStatus.UNAVAILABLE.value
    assert terminal.correlation_id == correlation_id
    assert terminal.parent_observation_id == parent_observation_id
    assert terminal.session_id == session_id
    assert terminal.interaction_id == interaction_id
