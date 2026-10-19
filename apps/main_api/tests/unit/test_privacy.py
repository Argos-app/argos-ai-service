from uuid import uuid4

from app.application.contracts.interactions import MessageCommand
from app.application.session_service import _normalize_input
from app.privacy import merge_recipient_selectors, minimize_pii, protect_user_input


def test_protect_user_input_keeps_email_visible_and_also_extracts_recipient_selector():
    protected = protect_user_input(
        "Envie o relatório para ana@example.com e cópia para ana@example.com"
    )

    assert protected.sanitized_text == (
        "Envie o relatório para ana@example.com e cópia para ana@example.com"
    )
    assert protected.recipient_selectors == {
        "[DESTINATARIO_1]": {"kind": "email", "value": "ana@example.com"}
    }
    assert protected.send_intent_requested is True


def test_protect_user_input_extracts_comma_separated_email_recipient_list():
    emails = (
        "luis.medeiros@institutojef.org.br",
        "leticia.camargo@institutojef.org.br",
        "beatriz.inacio@institutojef.org.br",
        "enzo.alves@institutojef.org.br",
        "vinicius.doliveira@institutojef.org.br",
    )
    protected = protect_user_input(
        "Prepare e envie o relatório. Envie exclusivamente para estes cinco "
        "destinatários cadastrados: " + ", ".join(emails) + ". Resolva-os pelo cadastro."
    )

    assert len(protected.recipient_selectors) == len(emails)
    assert {item["value"] for item in protected.recipient_selectors.values()} == set(emails)
    assert all(email in protected.sanitized_text for email in emails)
    assert "[EMAIL]" not in protected.sanitized_text
    assert protected.send_intent_requested is True


def test_email_clarification_captures_addresses_without_send_intent_or_para_prefix():
    protected = protect_user_input(
        "Quem são os destinatários do relatório? ana@example.com, bruno@example.com"
    )

    assert protected.send_intent_requested is False
    assert {item["value"] for item in protected.recipient_selectors.values()} == {
        "ana@example.com",
        "bruno@example.com",
    }
    assert "ana@example.com" in protected.sanitized_text
    assert "bruno@example.com" in protected.sanitized_text


def test_recipient_addresses_are_captured_without_nearby_email_wording():
    emails = (
        "luis.medeiros@institutojef.org.br",
        "leticia.camargo@institutojef.org.br",
        "beatriz.inacio@institutojef.org.br",
        "enzo.alves@institutojef.org.br",
        "vinicius.doliveira@institutojef.org.br",
    )
    protected = protect_user_input("Use estes cinco usuários cadastrados: " + ", ".join(emails))

    assert {item["value"] for item in protected.recipient_selectors.values()} == set(emails)
    assert all(email in protected.sanitized_text for email in emails)
    assert protected.send_intent_requested is False


def test_protect_user_input_redacts_non_recipient_pii_and_captures_quoted_name():
    protected = protect_user_input(
        'Prepare, mas não envie para "Ana Silva"; CPF 123.456.789-00, '
        "email de contato contato@example.com, token=abc123"
    )

    assert "Ana Silva" not in protected.sanitized_text
    assert "contato@example.com" in protected.sanitized_text
    assert "123.456.789-00" not in protected.sanitized_text
    assert "abc123" not in protected.sanitized_text
    assert "[DESTINATARIO_1]" in protected.sanitized_text
    assert "[EMAIL]" not in protected.sanitized_text
    assert "[CPF]" in protected.sanitized_text
    assert "[REDACTED]" in protected.sanitized_text
    assert protected.recipient_selectors == {
        "[DESTINATARIO_1]": {"kind": "name", "value": "Ana Silva"},
        "[DESTINATARIO_2]": {"kind": "email", "value": "contato@example.com"},
    }


def test_business_product_name_after_para_is_not_treated_as_email_recipient():
    text = "Consulte a ficha do medicamento para Ivervet Teste e informe a carência."

    protected = protect_user_input(text)

    assert protected.sanitized_text == text
    assert protected.recipient_selectors == {}


def test_quoted_medicine_name_is_preserved_outside_email_workflows():
    for quotes in (("“", "”"), ('"', '"')):
        text = f"Consulte o catálogo e as bulas para {quotes[0]}Vacina Teste A{quotes[1]}."
        protected = protect_user_input(text)
        assert protected.sanitized_text == text
        assert protected.recipient_selectors == {}
        assert protected.send_intent_requested is False


def test_all_names_in_an_email_recipient_list_are_protected():
    protected = protect_user_input(
        "Envie o relatório para Luis Medeiros, para Leticia Camargo, "
        "para Beatriz Inacio, para Enzo Alves e para Vinicius D'Oliveira"
    )

    assert "Luis Medeiros" not in protected.sanitized_text
    assert "Leticia Camargo" not in protected.sanitized_text
    assert "Beatriz Inacio" not in protected.sanitized_text
    assert "Enzo Alves" not in protected.sanitized_text
    assert "Vinicius D'Oliveira" not in protected.sanitized_text
    assert len(protected.recipient_selectors) == 5
    assert protected.send_intent_requested is True


def test_comma_separated_email_recipients_are_all_protected():
    protected = protect_user_input(
        "Envie o relatório por e-mail para Luis Medeiros, Leticia Camargo, "
        "Beatriz Inacio, Enzo Alves e Vinicius D'Oliveira. Peça minha confirmação."
    )

    assert all(
        name not in protected.sanitized_text
        for name in (
            "Luis Medeiros",
            "Leticia Camargo",
            "Beatriz Inacio",
            "Enzo Alves",
            "Vinicius D'Oliveira",
        )
    )
    assert [selector["value"] for selector in protected.recipient_selectors.values()] == [
        "Luis Medeiros",
        "Leticia Camargo",
        "Beatriz Inacio",
        "Enzo Alves",
        "Vinicius D'Oliveira",
    ]
    assert protected.send_intent_requested is True


def test_email_recipient_list_accepts_user_determiners():
    protected = protect_user_input(
        "Prepare o relatório para os usuários Luis Medeiros, Leticia Camargo, "
        "Beatriz Inacio, Enzo Alves e Vinicius D'Oliveira. Não envie ainda."
    )

    assert protected.send_intent_requested is False
    assert [selector["value"] for selector in protected.recipient_selectors.values()] == [
        "Luis Medeiros",
        "Leticia Camargo",
        "Beatriz Inacio",
        "Enzo Alves",
        "Vinicius D'Oliveira",
    ]
    assert all(
        name not in protected.sanitized_text
        for name in (
            "Luis Medeiros",
            "Leticia Camargo",
            "Beatriz Inacio",
            "Enzo Alves",
            "Vinicius D'Oliveira",
        )
    )


def test_minimize_pii_redacts_cnpj_and_brazilian_phone():
    sanitized = minimize_pii(
        "CNPJ 12.345.678/0001-90, telefone (11) 98765-4321, contato a@example.com"
    )

    assert "12.345.678/0001-90" not in sanitized
    assert "(11) 98765-4321" not in sanitized
    assert "[CNPJ]" in sanitized
    assert "[TELEFONE]" in sanitized
    assert "a@example.com" in sanitized


def test_minimize_pii_preserves_uuid_identifiers_while_redacting_phone():
    identifier = "c59bfd69-e647-b7dc-2b44-3c0f2f930b21"
    sanitized = minimize_pii(f"medicine_id={identifier}, contato (11) 98765-4321")

    assert identifier in sanitized
    assert "(11) 98765-4321" not in sanitized
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


def test_session_input_persists_email_without_anonymization():
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
    assert "ana@example.com" in sanitized_input
    assert "ana@example.com" in payload_json
    assert "Ana Silva" not in sanitized_input
    assert "123.456.789-00" not in payload_json
    assert "[DESTINATARIO_1]" not in sanitized_input
    assert recipient_selectors["[DESTINATARIO_1]"]["value"] == "ana@example.com"
