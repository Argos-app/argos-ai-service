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


def test_blocked_answer_preserves_pending_question(monkeypatch):
    from app.graph import input_risk
    from langchain_core.messages import HumanMessage

    state = {
        "input_kind": "answer",
        "question_id": "question-1",
        "clarification_request": object(),
        "errors": [],
    }
    candidate = {
        "question_id": None,
        "clarification_request": None,
        "messages": [HumanMessage(content="resposta sanitizada")],
    }
    monkeypatch.setattr(input_risk, "run_input_guardrail_node", lambda _: candidate)
    evaluator = Evaluator(InputRiskDecision.BLOCK)
    result = asyncio.run(input_risk.run_input_risk_node(state, evaluator=evaluator))
    assert result["errors"] == ["input_risk_blocked"]
    assert "question_id" not in result
    assert "clarification_request" not in result
    assert evaluator.calls == ["resposta sanitizada"]


def test_deterministic_rejection_skips_evaluator(monkeypatch):
    from app.graph import input_risk

    rejection = {"final_answer": "rejeitada", "errors": ["invalid_input"]}
    monkeypatch.setattr(input_risk, "run_input_guardrail_node", lambda _: rejection)
    evaluator = Evaluator(InputRiskDecision.ALLOW)
    assert asyncio.run(input_risk.run_input_risk_node({}, evaluator=evaluator)) == rejection
    assert evaluator.calls == []


def test_cancellation_is_not_a_fallback():
    class CancelledEvaluator:
        async def evaluate(self, text):
            raise asyncio.CancelledError()

    fallback = Evaluator(InputRiskDecision.ALLOW)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(FallbackInputRiskEvaluator(CancelledEvaluator(), fallback).evaluate("texto"))
    assert fallback.calls == []


@pytest.mark.parametrize(
    "provider,answers,expected",
    [
        (
            "jev",
            {"injection": {"type": "noul", "noul": 0.1}, "abuse": {"type": "noul", "noul": 0.2}},
            InputRiskDecision.ALLOW,
        ),
        (
            "jev",
            {"injection": {"type": "noul", "noul": 0.8}, "abuse": {"type": "noul", "noul": 0.2}},
            InputRiskDecision.BLOCK,
        ),
        (
            "openai",
            [
                {"name": "injection", "type": "predicate", "probability": 0.1},
                {"name": "abuse", "type": "predicate", "probability": 0.9},
            ],
            InputRiskDecision.BLOCK,
        ),
    ],
)
def test_http_decisions(monkeypatch, provider, answers, expected):
    import httpx
    from app.infrastructure.agents import input_risk_providers as adapters

    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            return httpx.Response(
                200, json={"answers": answers}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(adapters.httpx, "AsyncClient", Client)
    evaluator = adapters.DecisionInputRiskEvaluator(
        url="https://example.test", api_key="key", model="model", provider=provider
    )
    assert asyncio.run(evaluator.evaluate("entrada")) == expected
    assert requests[0]["model"] == "model"


@pytest.mark.parametrize(
    "answers",
    [
        [
            {"name": "injection", "type": "refusal"},
            {"name": "abuse", "type": "predicate", "probability": 0.1},
        ],
        [
            {"name": "injection", "type": "predicate", "probability": 0.1},
            {"name": "injection", "type": "predicate", "probability": 0.1},
        ],
        [
            {"name": "injection", "type": "predicate", "probability": True},
            {"name": "abuse", "type": "predicate", "probability": 0.1},
        ],
    ],
)
def test_http_malformed_answer_is_failure(monkeypatch, answers):
    import httpx
    from app.infrastructure.agents import input_risk_providers as adapters

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return httpx.Response(
                200, json={"answers": answers}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(adapters.httpx, "AsyncClient", Client)
    evaluator = adapters.DecisionInputRiskEvaluator(
        url="https://example.test", api_key="key", model="model", provider="openai"
    )
    with pytest.raises((ValueError, KeyError)):
        asyncio.run(evaluator.evaluate("entrada"))


def test_http_attempt_is_recorded_without_input(monkeypatch):
    import httpx
    from app.infrastructure.agents import input_risk_providers as adapters

    observations = []

    class Collector:
        def emit(self, observation):
            observations.append(observation)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "injection": {"type": "noul", "noul": 0.1},
                        "abuse": {"type": "noul", "noul": 0.2},
                    },
                    "usage": {"input_tokens": 20, "output_tokens": 5},
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(adapters.httpx, "AsyncClient", Client)
    evaluator = adapters.DecisionInputRiskEvaluator(
        url="https://example.test",
        api_key="secret",
        model="model",
        provider="jev",
        collector=Collector(),
    )
    assert asyncio.run(evaluator.evaluate("texto privado")) == InputRiskDecision.ALLOW
    assert len(observations) == 2
    terminal = observations[-1]
    assert terminal.result.code == "allow"
    assert terminal.usage.input_tokens == 20
    assert terminal.provider == "jev"
    assert "texto privado" not in terminal.model_dump_json()
    assert "secret" not in terminal.model_dump_json()


def test_http_timeout_invokes_fallback(monkeypatch):
    import httpx
    from app.infrastructure.agents import input_risk_providers as adapters

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(adapters.httpx, "AsyncClient", Client)
    primary = adapters.DecisionInputRiskEvaluator(
        url="https://example.test", api_key="key", model="model", provider="jev"
    )
    fallback = Evaluator(InputRiskDecision.BLOCK)
    assert (
        asyncio.run(FallbackInputRiskEvaluator(primary, fallback).evaluate("texto"))
        == InputRiskDecision.BLOCK
    )
    assert fallback.calls == ["texto"]


@pytest.mark.parametrize("outcome", [InputRiskDecision.ALLOW, InputRiskDecision.BLOCK])
def test_failure_of_primary_returns_fallback_outcome(outcome):
    fallback = Evaluator(outcome)
    assert (
        asyncio.run(FallbackInputRiskEvaluator(Evaluator(ValueError()), fallback).evaluate("texto"))
        == outcome
    )
    assert fallback.calls == ["texto"]


def test_invalid_primary_outcome_calls_fallback():
    fallback = Evaluator(InputRiskDecision.ALLOW)
    assert (
        asyncio.run(FallbackInputRiskEvaluator(Evaluator("invalid"), fallback).evaluate("texto"))
        == InputRiskDecision.ALLOW
    )
    assert fallback.calls == ["texto"]
