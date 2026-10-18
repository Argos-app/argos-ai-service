from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.agent_schemas.inputs import SpecialistInput
from app.agents import specialists
from app.agents.registry import ModelPolicyId
from app.agents.specialists import build_agent_factory
from app.infrastructure.agents.langchain_factory import AgentResponseError
from app.infrastructure.observability.model_callbacks import ModelUsageCallback
from app.ports.agent import AgentId

from packages.observability import (
    BestEffortObservationCollector,
    ObservationStatus,
    OperationKind,
    UsageAvailability,
    UsageSource,
)
from packages.observability.testing import InMemoryObservationWriter


def _model_response(usage, *, returned_model="gpt-safe"):
    message = SimpleNamespace(
        usage_metadata=usage,
        response_metadata={"model_name": returned_model},
    )
    return SimpleNamespace(
        generations=[[SimpleNamespace(message=message)]],
        llm_output={"token_usage": {"prompt_tokens": 700, "completion_tokens": 50}},
    )


@pytest.mark.asyncio
async def test_callback_records_each_model_call_with_authoritative_sdk_usage_only():
    writer = InMemoryObservationWriter()
    callback = ModelUsageCallback(BestEffortObservationCollector(writer), agent="farm")
    serialized = {
        "id": ["langchain", "openai", "chat_models", "ChatOpenAI"],
        "kwargs": {"model": "gpt-safe"},
    }
    first_id = uuid4()
    second_id = uuid4()

    for run_id, counts in (
        (first_id, {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}),
        (second_id, {"input_tokens": 30, "output_tokens": 8, "total_tokens": 38}),
    ):
        await callback.on_chat_model_start(serialized, [["private prompt"]], run_id=run_id)
        await callback.on_llm_end(_model_response(counts), run_id=run_id)

    terminal = [item for item in writer.observations if item.status != ObservationStatus.STARTED]
    assert len(terminal) == 2
    assert all(item.operation_kind == OperationKind.MODEL for item in terminal)
    assert all(item.provider == "openai" for item in terminal)
    assert all(item.model_requested == "gpt-safe" for item in terminal)
    assert all(item.model_returned == "gpt-safe" for item in terminal)
    assert [item.usage.input_tokens for item in terminal] == [12, 30]
    assert [item.usage.output_tokens for item in terminal] == [4, 8]
    assert all(item.usage.source == UsageSource.SDK for item in terminal)
    assert all(item.usage.availability == UsageAvailability.RETURNED for item in terminal)
    assert "private prompt" not in repr(writer.observations)
    assert "700" not in repr(terminal[0].usage.model_dump())


@pytest.mark.asyncio
async def test_callback_falls_back_to_provider_usage_and_records_missing_usage():
    writer = InMemoryObservationWriter()
    callback = ModelUsageCallback(BestEffortObservationCollector(writer))
    serialized = {"id": ["langchain", "anthropic", "ChatAnthropic"], "kwargs": {}}

    provider_run = uuid4()
    await callback.on_chat_model_start(serialized, [], run_id=provider_run)
    provider_message = SimpleNamespace(
        usage_metadata=None,
        response_metadata={
            "usage": {
                "input_tokens": 5,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 1,
            }
        },
    )
    await callback.on_llm_end(
        SimpleNamespace(generations=[[SimpleNamespace(message=provider_message)]], llm_output={}),
        run_id=provider_run,
    )

    absent_run = uuid4()
    await callback.on_chat_model_start(serialized, [], run_id=absent_run)
    no_usage_message = SimpleNamespace(usage_metadata=None, response_metadata={})
    await callback.on_llm_end(
        SimpleNamespace(generations=[[SimpleNamespace(message=no_usage_message)]], llm_output={}),
        run_id=absent_run,
    )

    terminal = [item for item in writer.observations if item.status != ObservationStatus.STARTED]
    assert terminal[0].usage.source == UsageSource.PROVIDER
    assert terminal[0].usage.cache_read_tokens == 3
    assert terminal[0].usage.cache_write_tokens == 1
    assert terminal[1].usage.availability == UsageAvailability.ABSENT
    assert terminal[1].usage.input_tokens is None


@pytest.mark.asyncio
async def test_callback_records_provider_failure_without_error_message_contents():
    writer = InMemoryObservationWriter()
    callback = ModelUsageCallback(BestEffortObservationCollector(writer), agent="router")
    run_id = uuid4()
    await callback.on_llm_start(
        {"id": ["langchain", "openai", "ChatOpenAI"], "kwargs": {}},
        ["prompt containing private data"],
        run_id=run_id,
    )
    await callback.on_llm_error(RuntimeError("secret-bearing provider response"), run_id=run_id)

    terminal = writer.observations[-1]
    assert terminal.status == ObservationStatus.FAILED
    assert terminal.usage.availability == UsageAvailability.ABSENT
    assert terminal.error.origin.value == "provider"
    assert "secret-bearing" not in repr(writer.observations)


@pytest.mark.asyncio
async def test_model_usage_is_kept_when_agent_output_parsing_fails(monkeypatch):
    writer = InMemoryObservationWriter()
    collector = BestEffortObservationCollector(writer)
    response = _model_response({"input_tokens": 9, "output_tokens": 3, "total_tokens": 12})

    class RunnableWithInvalidStructuredOutput:
        async def ainvoke(self, _state, config=None):
            callback = next(
                handler
                for handler in config["callbacks"]
                if isinstance(handler, ModelUsageCallback)
            )
            run_id = uuid4()
            await callback.on_chat_model_start(
                {
                    "id": ["langchain", "openai", "chat_models", "ChatOpenAI"],
                    "kwargs": {"model": "gpt-safe"},
                },
                [],
                run_id=run_id,
            )
            await callback.on_llm_end(response, run_id=run_id)
            return {"structured_response": {"not_a_valid_specialist_result": True}}

    monkeypatch.setattr(
        specialists,
        "create_agent",
        lambda **_: RunnableWithInvalidStructuredOutput(),
    )
    models = {policy: object() for policy in ModelPolicyId}
    agent = build_agent_factory(
        models,
        observation_collector=collector,
        use_mock_tools=True,
    ).create(AgentId.FARM)

    with pytest.raises(AgentResponseError):
        await agent.run(
            SpecialistInput(
                task_id="task-1",
                goal="Consultar registros",
                brief_sanitized="Consulta de teste",
            )
        )

    model_terminal = next(
        item
        for item in writer.observations
        if item.operation_kind == OperationKind.MODEL and item.status != ObservationStatus.STARTED
    )
    assert model_terminal.status == ObservationStatus.SUCCEEDED
    assert model_terminal.usage.input_tokens == 9
