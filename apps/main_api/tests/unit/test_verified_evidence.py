import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.application.execution_limits import ExecutionLimits
from app.application.verified_evidence import verified_evidence_fallback


def test_timeout_fallback_preserves_only_verified_tool_output() -> None:
    limits = ExecutionLimits(max_model_calls=4, max_tool_calls=4)
    limits.record_tool_success(
        "analyst",
        "estimate_medicine_consumption",
        '{"status":"estimated","data_origin":"synthetic","items":'
        '[{"medicine_name":"Vacina Teste A","estimated_quantity":"52.9","unit":"ml"}]}',
        reference="tool-call:prediction-1",
    )

    answer = verified_evidence_fallback(
        limits.evidence_snapshot(),
        unavailable_stage="síntese",
    )

    assert answer is not None
    assert "síntese pelo modelo não foi concluída" in answer
    assert "estimate_medicine_consumption" in answer
    assert '"data_origin": "synthetic"' in answer
    assert '"estimated_quantity": "52.9"' in answer
    assert "previsão real" not in answer


def test_timeout_fallback_does_not_promote_abstention_or_empty_results() -> None:
    limits = ExecutionLimits(max_model_calls=4, max_tool_calls=4)
    limits.record_tool_success(
        "information",
        "consultar_documental",
        '{"status":"success","content":{"status":"abstain","answer":"",'
        '"sources":[]}}',
    )

    assert (
        verified_evidence_fallback(
            limits.evidence_snapshot(),
            unavailable_stage="processamento",
        )
        is None
    )
