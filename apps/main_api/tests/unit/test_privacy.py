from uuid import uuid4

from app.application.contracts.interactions import MessageCommand
from app.application.session_service import _normalize_input
from app.privacy import merge_recipient_selectors, minimize_pii, protect_user_input


def test_protect_user_input_keeps_explicit_recipient_as_unique_reference():
    protected = protect_user_input(
        "Envie o relatório para ana@example.com e cópia para ana@example.com"
    )

    assert protected.sanitized_text == (
        "Envie o relatório para [DESTINATARIO_1] e cópia para [DESTINATARIO_1]"
    )
    assert protected.recipient_selectors == {
        "[DESTINATARIO_1]": {"kind": "email", "value": "ana@example.com"}
    }
    assert protected.send_intent_requested is True


def test_protect_user_input_redacts_non_recipient_pii_and_captures_quoted_name():
    protected = protect_user_input(
        'Prepare, mas não envie para "Ana Silva"; CPF 123.456.789-00, '
        "email de contato contato@example.com, token=abc123"
    )

    assert "Ana Silva" not in protected.sanitized_text
    assert "contato@example.com" not in protected.sanitized_text
    assert "123.456.789-00" not in protected.sanitized_text
    assert "abc123" not in protected.sanitized_text
    assert "[DESTINATARIO_1]" in protected.sanitized_text
    assert "[EMAIL]" in protected.sanitized_text
    assert "[CPF]" in protected.sanitized_text
    assert "[REDACTED]" in protected.sanitized_text
    assert protected.recipient_selectors == {
        "[DESTINATARIO_1]": {"kind": "name", "value": "Ana Silva"}
    }
    assert protected.send_intent_requested is False


def test_minimize_pii_redacts_cnpj_and_brazilian_phone():
    sanitized = minimize_pii("CNPJ 12.345.678/0001-90, telefone (11) 98765-4321")

    assert "12.345.678/0001-90" not in sanitized
    assert "(11) 98765-4321" not in sanitized
    assert "[CNPJ]" in sanitized
    assert "[TELEFONE]" in sanitized


def test_negated_send_language_does_not_record_an_explicit_send_intent():
    protected = protect_user_input("Não gostaria de enviar o relatório para Ana Silva")

    assert protected.send_intent_requested is False


def test_merge_recipient_selectors_does_not_overwrite_prior_clarification_context():
    merged, sanitized = merge_recipient_selectors(
        {"[DESTINATARIO_1]": {"kind": "email", "value": "old@example.com"}},
        {"[DESTINATARIO_1]": {"kind": "email", "value": "new@example.com"}},
        "Use [DESTINATARIO_1]",
    )

    assert sanitized == "Use [DESTINATARIO_2]"
    assert merged == {
        "[DESTINATARIO_1]": {"kind": "email", "value": "old@example.com"},
        "[DESTINATARIO_2]": {"kind": "email", "value": "new@example.com"},
    }


def test_session_input_persists_only_sanitized_recipient_marker():
    normalized = _normalize_input(
        MessageCommand(
            kind="message",
            client_message_id=uuid4(),
            message="Envie o relatório para Ana Silva <ana@example.com>, CPF 123.456.789-00",
        )
    )

    sanitized_input, payload_json, recipient_selectors = (
        normalized[2],
        normalized[3],
        normalized[6],
    )
    assert "ana@example.com" not in sanitized_input
    assert "ana@example.com" not in payload_json
    assert "Ana Silva" not in sanitized_input
    assert "123.456.789-00" not in payload_json
    assert "[DESTINATARIO_1]" in sanitized_input
    assert recipient_selectors["[DESTINATARIO_1]"]["value"] == "ana@example.com"
