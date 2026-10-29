"""Verifica aprovação, bloqueio e indisponibilidade dos avaliadores."""

import asyncio

import pytest
from app.application.input_risk import FallbackInputRiskEvaluator
from app.ports.input_risk import InputRiskDecision


class Evaluator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def evaluate(self, text):
        self.calls.append(text)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("decision", [InputRiskDecision.ALLOW, InputRiskDecision.BLOCK])
def test_primary_decision_does_not_call_fallback(decision):
    primary, fallback = Evaluator(decision), Evaluator(InputRiskDecision.ALLOW)
    result = asyncio.run(FallbackInputRiskEvaluator(primary, fallback).evaluate("Sim"))
    assert result == decision
    assert fallback.calls == []


def test_primary_failure_calls_fallback():
    fallback = Evaluator(InputRiskDecision.ALLOW)
    result = asyncio.run(
        FallbackInputRiskEvaluator(Evaluator(TimeoutError()), fallback).evaluate("Lote 12")
    )
    assert result == InputRiskDecision.ALLOW
    assert fallback.calls == ["Lote 12"]


def test_both_failures_return_unavailable():
    result = asyncio.run(
        FallbackInputRiskEvaluator(Evaluator(TimeoutError()), Evaluator(ValueError())).evaluate(
            "texto"
        )
    )
    assert result == InputRiskDecision.UNAVAILABLE
