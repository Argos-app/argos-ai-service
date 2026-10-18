import json
from uuid import uuid4

import pytest
from argos_a2a_agent.adapters.providers.answers import _answer_from_json, _answer_from_response
from argos_a2a_agent.agents.specialists import _uninterpreted_evidence_answer
from argos_a2a_agent.domain.agent_answers import AgentEvidence

from packages.contracts.a2a import DocumentAnswerStatus, DocumentSourceReference, DocumentType

_EVIDENCE_ID = "ev_0123456789abcdef01234567"


@pytest.mark.asyncio
async def test_nvidia_answer_request_disables_optional_thinking(monkeypatch):
    from argos_a2a_agent.adapters.providers import answers

    payloads = []

    class HTTP:
        def __init__(self, *args):
            pass

        async def post_json(self, payload, **kwargs):
            payloads.append(payload)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "Trecho verificável 1.",
                                    "status": "success",
                                    "cited_evidence_ids": [f"ev_{1:024x}"],
                                }
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(answers, "OpenAICompatibleHTTP", HTTP)
    generator = answers.OpenAICompatibleAnswerGenerator(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "test-secret",
        "nvidia/nemotron-3.5-super-vl-120b-a12b",
        40,
    )
    result = await generator.generate(
        system_prompt="Consulte a evidência.",
        question="Qual o trecho?",
        jurisdiction=None,
        reference_date=None,
        evidence=(_evidence(),),
    )
    assert payloads[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert result.status == DocumentAnswerStatus.SUCCESS


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
