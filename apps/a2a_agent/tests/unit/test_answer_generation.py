import json
from uuid import uuid4

import httpx
import pytest
from argos_a2a_agent.adapters.providers.answers import (
    OpenAICompatibleAnswerGenerator,
    ProviderFallbackAnswerGenerator,
    _answer_from_json,
    _answer_from_response,
    _is_transient_provider_failure,
)
from argos_a2a_agent.agents.specialists import _uninterpreted_evidence_answer
from argos_a2a_agent.domain.agent_answers import AgentEvidence

from packages.contracts.a2a import DocumentAnswerStatus, DocumentSourceReference, DocumentType

_EVIDENCE_ID = "ev_0123456789abcdef01234567"


def test_anthropic_fallback_uses_configured_endpoint():
    primary = OpenAICompatibleAnswerGenerator(
        "https://openai.example/v1/chat/completions", "key", "model", 1
    )
    fallback = ProviderFallbackAnswerGenerator(
        primary,
        "key",
        "model",
        1,
        endpoint="https://anthropic.example/v1/messages",
        max_output_tokens=1024,
    )

    assert fallback._endpoint == "https://anthropic.example/v1/messages"


def test_provider_fallback_classifier_accepts_only_transient_openai_errors():
    try:
        raise httpx.ConnectError("connection unavailable")
    except httpx.ConnectError as cause:
        transient = RuntimeError("OpenAI transport failed")
        transient.__cause__ = cause

    assert _is_transient_provider_failure(transient)
    assert not _is_transient_provider_failure(ValueError("invalid structured response"))


def test_openai_answer_uses_native_strict_json_schema():
    from argos_a2a_agent.adapters.providers import answers

    response_format = answers._structured_response_format()

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["required"] == [
        "answer",
        "status",
        "cited_evidence_ids",
        "limitations",
        "missing_fields",
    ]


def _evidence(index: int = 1) -> AgentEvidence:
    source = DocumentSourceReference(
        document_id=uuid4(),
        title=f"Documento sintético {index}",
        document_type=DocumentType.LAW,
        origin="local-test-fixture",
        revision_hash="a" * 64,
        page_from=1,
        page_to=1,
        excerpt=f"Trecho verificável {index}: campo ausente permanece desconhecido.",
        was_updated=False,
    )
    return AgentEvidence(
        evidence_id=f"ev_{index:024x}",
        source=source,
    )


@pytest.mark.asyncio
async def test_primary_and_fallback_send_the_same_document_context():
    evidence = (_evidence(),)
    answer = {
        "answer": f"Trecho [[{evidence[0].evidence_id}]]",
        "status": "success",
        "cited_evidence_ids": [evidence[0].evidence_id],
        "limitations": [],
        "missing_fields": [],
    }
    requests = {}
    primary = OpenAICompatibleAnswerGenerator("https://primary.example/chat", "key", "m", 1)
    fallback = ProviderFallbackAnswerGenerator(
        primary, "key", "m", 1, endpoint="https://fallback.example/messages", max_output_tokens=1024
    )

    async def primary_post(payload, **_kwargs):
        requests["primary"] = payload
        return {"choices": [{"message": {"content": json.dumps(answer)}}]}

    def fallback_post(request):
        requests["fallback"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": json.dumps(answer)}], "usage": {}},
        )

    primary._http.post_json = primary_post
    await fallback._client.aclose()
    fallback._client = httpx.AsyncClient(transport=httpx.MockTransport(fallback_post))
    try:
        kwargs = {
            "system_prompt": "Use as evidências.",
            "question": "Qual é o aviso?",
            "jurisdiction": None,
            "reference_date": None,
            "evidence": evidence,
        }
        await primary.generate(**kwargs)
        await fallback._generate_anthropic(**kwargs)
    finally:
        await primary.aclose()
        await fallback.aclose()

    primary_messages = requests["primary"]["messages"]
    fallback_payload = requests["fallback"]
    assert primary_messages[0]["content"] == fallback_payload["system"]
    assert json.loads(primary_messages[1]["content"]) == json.loads(
        fallback_payload["messages"][0]["content"]
    )


def test_model_abstention_with_retrieved_evidence_becomes_cited_partial_excerpts():
    evidence = (_evidence(),)

    answer = _uninterpreted_evidence_answer(evidence)

    assert answer is not None
    assert answer.status == DocumentAnswerStatus.PARTIAL
    assert answer.cited_evidence_ids == (evidence[0].evidence_id,)
    assert evidence[0].source.excerpt in answer.answer
    assert f"[[{evidence[0].evidence_id}]]" in answer.answer
    assert "sem interpretação adicional" in answer.answer
    assert "não uma resposta interpretada" in answer.limitations[0]


def test_evidence_fallback_is_bounded_and_returns_only_cited_sources():
    evidence = tuple(_evidence(index) for index in range(1, 5))

    answer = _uninterpreted_evidence_answer(evidence)

    assert answer is not None
    assert len(answer.cited_evidence_ids) == 3
    assert len(answer.answer) <= 6_500
    assert evidence[0].source.excerpt in answer.answer
    assert evidence[2].source.excerpt in answer.answer
    assert evidence[3].source.excerpt not in answer.answer


def test_evidence_fallback_does_not_create_an_answer_without_sources():
    assert _uninterpreted_evidence_answer(()) is None


def test_answer_parser_ignores_additional_fields_and_adds_missing_inline_citation():
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "answer": "O termo contém este aviso.",
                            "status": "success",
                            "cited_evidence_ids": [_EVIDENCE_ID],
                            "confidence": 0.9,
                        }
                    )
                }
            }
        ]
    }

    answer = _answer_from_response(response, {_EVIDENCE_ID})

    assert answer.status == DocumentAnswerStatus.SUCCESS
    assert answer.cited_evidence_ids == (_EVIDENCE_ID,)
    assert f"[[{_EVIDENCE_ID}]]" in answer.answer


def test_answer_parser_recovers_citation_ids_from_inline_markers():
    answer = _answer_from_json(
        {
            "answer": f"O termo contém este aviso [[{_EVIDENCE_ID}]].",
            "status": "success",
        }
    )

    assert answer.cited_evidence_ids == (_EVIDENCE_ID,)


def test_invalid_provider_json_becomes_explicit_abstention():
    answer = _answer_from_response(
        {"choices": [{"message": {"content": "A resposta não veio em JSON."}}]},
        {_EVIDENCE_ID},
    )

    assert answer.status == DocumentAnswerStatus.ABSTAIN
    assert answer.answer == ""
    assert "não foi validado" in answer.limitations[0]


def test_unknown_evidence_reference_becomes_explicit_abstention():
    answer = _answer_from_response(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": "O termo contém este aviso.",
                                "status": "success",
                                "cited_evidence_ids": ["ev_ffffffffffffffffffffffff"],
                            }
                        )
                    }
                }
            ]
        },
        {_EVIDENCE_ID},
    )

    assert answer.status == DocumentAnswerStatus.ABSTAIN
    assert answer.answer == ""
    assert "não foi validado" in answer.limitations[0]


def test_answer_parser_accepts_plain_text_only_when_it_cites_retrieved_evidence():
    answer = _answer_from_response(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            f"O documento é demonstrativo, sem regra real. [[{_EVIDENCE_ID}]]"
                        )
                    }
                }
            ]
        },
        {_EVIDENCE_ID},
    )

    assert answer.status == DocumentAnswerStatus.SUCCESS
    assert answer.cited_evidence_ids == (_EVIDENCE_ID,)


def test_answer_parser_accepts_citation_objects_and_infers_status_from_valid_evidence():
    answer = _answer_from_response(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "response": "O documento é demonstrativo.",
                                "citations": [{"evidence_id": _EVIDENCE_ID}],
                            }
                        )
                    }
                }
            ]
        },
        {_EVIDENCE_ID},
    )

    assert answer.status == DocumentAnswerStatus.SUCCESS
    assert answer.cited_evidence_ids == (_EVIDENCE_ID,)
    assert f"[[{_EVIDENCE_ID}]]" in answer.answer


def test_answer_parser_normalizes_wrapped_ids_in_provider_citation_fields():
    answer = _answer_from_json(
        {
            "answer": "O documento contém a ressalva solicitada.",
            "status": "success",
            "cited_evidence_ids": [f"[[{_EVIDENCE_ID}]]"],
        }
    )

    assert answer.status == DocumentAnswerStatus.SUCCESS
    assert answer.cited_evidence_ids == (_EVIDENCE_ID,)
    assert f"[[{_EVIDENCE_ID}]]" in answer.answer
