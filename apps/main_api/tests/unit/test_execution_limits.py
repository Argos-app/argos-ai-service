from uuid import uuid4

import pytest
from app.infrastructure.agents.execution_limits import (
    ExecutionLimitCallback,
    ExecutionLimitExceeded,
    ExecutionLimits,
)


def test_call_and_cumulative_tool_output_budgets_fail_closed():
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1, max_tool_output_chars=5)

    limits.reserve_model_call()
    with pytest.raises(ExecutionLimitExceeded, match="model_call_budget"):
        limits.reserve_model_call()
    limits.reserve_tool_call()
    with pytest.raises(ExecutionLimitExceeded, match="tool_call_budget"):
        limits.reserve_tool_call()
    limits.reserve_tool_output("12345")
    with pytest.raises(ExecutionLimitExceeded, match="tool_output_budget"):
        limits.reserve_tool_output("x")


@pytest.mark.asyncio
async def test_callback_counts_model_and_tool_calls_and_records_only_real_tool_evidence():
    limits = ExecutionLimits(max_model_calls=2, max_tool_calls=2)
    callback = ExecutionLimitCallback(limits, "analyst")
    model_run = uuid4()
    await callback.on_chat_model_start({}, [], run_id=model_run)
    await callback.on_llm_start({}, [], run_id=model_run)
    assert limits.model_calls == 1

    for run_id, name in ((uuid4(), "estimate_medicine_consumption"), (uuid4(), "mock_prediction")):
        await callback.on_tool_start({"name": name}, "", run_id=run_id)
        await callback.on_tool_end("{}", run_id=run_id)

    evidence = limits.evidence_for("analyst")
    assert len(evidence) == 1
    assert evidence[0][0] == "estimate_medicine_consumption"
