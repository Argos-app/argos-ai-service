import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.sessions import (
    ConversationMessage,
    MessageKind,
    MessageRole,
)
from app.infrastructure.agents.session_summary_generator import (
    SUMMARY_SYSTEM_PROMPT,
    LangChainSessionSummaryGenerator,
    SummaryGenerationError,
)
from app.infrastructure.agents.simulated_model import SimulatedChatModel
from app.session_config import SESSION_SUMMARY_MAX_TOKENS


class FakeStructuredModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        system = messages[0].content
        if system.strip().startswith("Compacte"):
            return {
                "activity": "Consultar aplicações da fazenda.",
                "important_facts": [
                    {
                        "content": "15 aplicações no período.",
                        "source_references": ["farm://15"],
                    }
                ],
                "decisions": ["Analisar o período informado."],
                "pending_activity": None,
                "limitations": ["Dados válidos até a última consulta."],
            }
        return self.response


class FakeModel:
    def __init__(self, response, *, count_chars=False):
        self.structured = FakeStructuredModel(response)
        self.count_chars = count_chars

    def with_structured_output(self, schema):
        assert schema.__name__ == "SessionSummaryDraft"
        return self.structured

    def get_num_tokens(self, text):
        return len(text) if self.count_chars else len(text.split())


def _message(content: str):
    now = datetime.now(UTC)
    return ConversationMessage(
        message_id=uuid4(),
        session_id=uuid4(),
        interaction_id=uuid4(),
        sequence=1,
        role=MessageRole.USER,
        kind=MessageKind.USER_INPUT,
        sanitized_content=content,
        created_at=now,
        user_message_number=1,
    )


@pytest.mark.asyncio
async def test_generator_publishes_structured_summary_with_measured_token_count():
    model = FakeModel(
        {
            "activity": "Consultar aplicações da fazenda.",
            "important_facts": [
                {"content": "15 aplicações no período.", "source_references": ["farm://15"]}
            ],
            "decisions": ["Analisar o período informado."],
            "pending_activity": None,
            "limitations": ["Dados válidos até a última consulta."],
        }
    )
    generator = LangChainSessionSummaryGenerator(model, input_token_limit=8_000)
    session_id = uuid4()

    summary = await generator.generate(
        session_id=session_id,
        previous=None,
        messages=(_message("Quantas aplicações houve no período?"),),
        events=(),
        through_user_message_number=15,
        max_tokens=SESSION_SUMMARY_MAX_TOKENS,
    )

    assert summary.session_id == session_id
    assert summary.through_user_message_number == 15
    assert summary.important_facts[0].source_references == ("farm://15",)
    assert summary.token_count == model.get_num_tokens(summary.summary_text)


def test_simulated_model_counts_tokens_deterministically_without_provider():
    model = SimulatedChatModel()

    assert model.get_num_tokens("") == 0
    assert model.get_num_tokens("ab cde") == 2
    assert model.get_num_tokens("ab cde") == model.get_num_tokens("ab cde")


@pytest.mark.asyncio
async def test_simulated_model_generates_a_valid_session_summary():
    model = SimulatedChatModel()
    generator = LangChainSessionSummaryGenerator(model, input_token_limit=8_000)
    session_id = uuid4()

    summary = await generator.generate(
        session_id=session_id,
        previous=None,
        messages=(_message("Marcador sintético para validar o resumo da sessão."),),
        events=(),
        through_user_message_number=15,
        max_tokens=SESSION_SUMMARY_MAX_TOKENS,
    )

    assert summary.session_id == session_id
    assert summary.through_user_message_number == 15
    assert "Marcador sintético" in summary.activity
    assert any("modo simulado" in item for item in summary.limitations)
    assert summary.token_count == model.get_num_tokens(summary.summary_text)


@pytest.mark.asyncio
async def test_generator_compacts_over_budget_output_without_truncation():
    model = FakeModel(
        {
            "activity": "atividade " + "importante " * 250,
            "important_facts": [],
            "decisions": [],
            "pending_activity": None,
            "limitations": [],
        },
        count_chars=True,
    )
    generator = LangChainSessionSummaryGenerator(model, input_token_limit=20_000)

    summary = await generator.generate(
        session_id=uuid4(),
        previous=None,
        messages=(_message("Pedido longo que precisa ser mantido integralmente."),),
        events=(),
        through_user_message_number=15,
        max_tokens=SESSION_SUMMARY_MAX_TOKENS,
    )

    assert len(model.structured.calls) == 2
    assert "15 aplicações" in summary.summary_text
    assert summary.token_count <= SESSION_SUMMARY_MAX_TOKENS


@pytest.mark.asyncio
async def test_generator_rejects_oversized_single_interaction_without_truncating():
    model = FakeModel(
        {
            "activity": "Atividade.",
            "important_facts": [],
            "decisions": [],
            "pending_activity": None,
            "limitations": [],
        },
        count_chars=True,
    )
    generator = LangChainSessionSummaryGenerator(model, input_token_limit=1)

    with pytest.raises(SummaryGenerationError, match="nada foi truncado"):
        await generator.generate(
            session_id=uuid4(),
            previous=None,
            messages=(_message("Manter este conteúdo sem truncamento."),),
            events=(),
            through_user_message_number=15,
            max_tokens=SESSION_SUMMARY_MAX_TOKENS,
        )
    assert model.structured.calls == []


@pytest.mark.asyncio
async def test_generator_splits_oversized_message_into_lossless_token_bounded_parts():
    content = "Informação importante. " * 300
    model = FakeModel(
        {
            "activity": "Preservar informação importante.",
            "important_facts": [],
            "decisions": [],
            "pending_activity": None,
            "limitations": [],
        },
        count_chars=True,
    )
    generator = LangChainSessionSummaryGenerator(model, input_token_limit=2_000)

    summary = await generator.generate(
        session_id=uuid4(),
        previous=None,
        messages=(_message(content),),
        events=(),
        through_user_message_number=15,
        max_tokens=SESSION_SUMMARY_MAX_TOKENS,
    )

    captured = []
    for call in model.structured.calls:
        payload = json.loads(call[1].content)
        captured.extend(item["content"] for item in payload["new_interactions"])
        assert len(SUMMARY_SYSTEM_PROMPT + call[1].content) <= 2_000
    assert "".join(captured) == content
    assert len(model.structured.calls) > 1
    assert summary.token_count <= SESSION_SUMMARY_MAX_TOKENS
