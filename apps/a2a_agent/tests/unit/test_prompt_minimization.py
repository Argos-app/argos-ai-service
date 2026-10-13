import pytest
from argos_a2a_agent.adapters.providers.answers import (
    OpenAICompatibleAnswerGenerator,
    _minimize_prompt_value,
)


def test_external_model_payload_minimizes_contact_and_secret_values_recursively():
    sanitized = _minimize_prompt_value(
        {
            "question": "Ligue para (11) 98765-4321, CPF 123.456.789-00",
            "evidence": [{"excerpt": "Contato: ana@example.com, CNPJ 12.345.678/0001-90"}],
            "other": "api_key=abc123",
        }
    )

    text = str(sanitized)
    assert "(11) 98765-4321" not in text
    assert "123.456.789-00" not in text
    assert "ana@example.com" not in text
    assert "12.345.678/0001-90" not in text
    assert "abc123" not in text
    assert "[TELEFONE]" in text
    assert "[CPF]" in text
    assert "[EMAIL]" in text
    assert "[CNPJ]" in text
    assert "[REDACTED]" in text


@pytest.mark.parametrize("value", [127, 8193])
def test_answer_model_output_limit_is_validated(value):
    with pytest.raises(ValueError, match="max_output_tokens"):
        OpenAICompatibleAnswerGenerator(
            "https://models.example/v1",
            "test-key",
            "test-model",
            2,
            max_output_tokens=value,
        )
