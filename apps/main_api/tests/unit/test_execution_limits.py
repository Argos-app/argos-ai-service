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
        output = (
            '{"contact": "person@example.com"}' if name == "estimate_medicine_consumption" else "{}"
        )
        await callback.on_tool_end(output, run_id=run_id)

    evidence = limits.evidence_for("analyst")
    assert len(evidence) == 1
    assert evidence[0].tool_name == "estimate_medicine_consumption"
    assert "person@example.com" in evidence[0].excerpt


@pytest.mark.asyncio
async def test_tool_evidence_is_bounded_and_excludes_secretary_payloads():
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1, max_evidence_chars=12)
    limits.record_tool_success("analyst", "lookup", "12345678901234567890")
    limits.record_tool_success("secretary", "prepare_report", "recipient@example.com")

    evidence = limits.evidence_for("analyst")
    assert len(evidence) == 1
    assert evidence[0].excerpt == "123456789012"
    assert limits.evidence_for("secretary") == ()


def test_tool_evidence_preserves_large_excerpts_within_interaction_budget():
    excerpt = "x" * 12_000
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1)

    limits.record_tool_success("analyst", "estimate", excerpt)

    assert limits.evidence_for("analyst")[0].excerpt == excerpt
    assert len(limits.evidence_for("analyst")[0].excerpt) > 6_000


def test_tool_evidence_budget_caps_the_combined_excerpts():
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1, max_evidence_chars=10)

    limits.record_tool_success("farm", "first", "123456")
    limits.record_tool_success("analyst", "second", "abcdef")

    evidence = limits.evidence_snapshot()
    assert [item.excerpt for item in evidence] == ["123456", "abcd"]
    assert sum(len(item.excerpt) for item in evidence) == 10


@pytest.mark.parametrize("status", ["abstain", "unavailable", "error", "failed"])
def test_unavailable_tool_response_is_not_promoted_to_factual_evidence(status):
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1)

    limits.record_tool_success(
        "information",
        "consultar_documental",
        f'{{"status":"{status}","answer":"","sources":[],"limitations":["indisponível"]}}',
    )

    assert limits.evidence_for("information") == ()


def test_nested_abstention_status_is_available_for_safe_fallback_only():
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1)

    limits.record_tool_success(
        "information",
        "consultar_documental",
        '{"status":"success","content":{"status":"abstain","answer":"",'
        '"limitations":["conteúdo não validado"]}}',
        reference="tool-call:documental-1",
    )

    result = limits.latest_non_evidence_result("information")
    assert result is not None
    assert result.status == "abstain"
    assert result.tool_name == "consultar_documental"
    assert result.reference == "tool-call:documental-1"
    assert limits.evidence_for("information") == ()


def test_evidence_snapshot_preserves_tool_completion_order_across_agents():
    limits = ExecutionLimits(max_model_calls=1, max_tool_calls=1)

    limits.record_tool_success("farm", "list_lots", '{"head_count":49}')
    limits.record_tool_success("analyst", "estimate", '{"estimate":28}')
    limits.record_tool_success("farm", "list_properties", '{"count":2}')

    assert [item.tool_name for item in limits.evidence_snapshot()] == [
        "list_lots",
        "estimate",
        "list_properties",
    ]
