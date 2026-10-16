import json
from uuid import uuid4

import pytest
from app.domain.memory import MemoryFieldAction
from app.infrastructure.agents.user_memory_extractor import (
    USER_MEMORY_SYSTEM_PROMPT,
    LangChainUserMemoryExtractor,
)


class FakeStructuredModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.response


class FakeModel:
    def __init__(self, response):
        self.structured = FakeStructuredModel(response)

    def with_structured_output(self, schema):
        assert schema.__name__ == "UserMemoryDraft"
        return self.structured


@pytest.mark.asyncio
async def test_extractor_uses_structured_output_and_redacts_secrets_from_model_input():
    model = FakeModel(
        {
            "tone": {
                "action": "set",
                "value": "linguagem simples",
                "source_excerpt": "Prefiro linguagem simples.",
            },
            "general_goal": {"action": "unchanged", "value": None, "source_excerpt": None},
        }
    )
    extractor = LangChainUserMemoryExtractor(model)

    change = await extractor.extract(
        user_id=uuid4(),
        session_id=uuid4(),
        sanitized_user_input="Prefiro linguagem simples. api_key=secret-value",
        current=None,
    )

    payload = json.loads(model.structured.calls[0][1].content)
    assert change.tone.action == MemoryFieldAction.SET
    assert payload["user_entry"].endswith("api_key=[REDACTED]")
    assert "secret-value" not in model.structured.calls[0][1].content
    assert "não infira preferências por perguntas" in USER_MEMORY_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_extractor_maps_delete_action_for_service_side_authorization():
    model = FakeModel(
        {
            "tone": {"action": "delete", "value": None, "source_excerpt": None},
            "general_goal": {"action": "unchanged", "value": None, "source_excerpt": None},
        }
    )
    extractor = LangChainUserMemoryExtractor(model)

    change = await extractor.extract(
        user_id=uuid4(),
        session_id=uuid4(),
        sanitized_user_input="Esqueça meu tom.",
        current=None,
    )

    assert change.tone.action == MemoryFieldAction.DELETE
