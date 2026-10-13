"""Testes dos contratos de domínio do serviço documental A2A."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.a2a import (
    CorpusRefreshStatus,
    DocumentAnswerResponse,
    DocumentAnswerStatus,
    DocumentQuestionRequest,
    DocumentSkill,
    DocumentSourceReference,
    DocumentType,
)


def question_payload(**overrides):
    return {
        "request_id": uuid4(),
        "question": "Quais são as condições do documento atual?",
        "skill": DocumentSkill.DOCUMENTAL,
        "deadline_at": datetime(2026, 10, 10, 14, 0, tzinfo=UTC),
        **overrides,
    }


def source_payload(**overrides):
    return {
        "document_id": uuid4(),
        "title": "Termos gerais",
        "document_type": DocumentType.TERMS_CONDITIONS,
        "origin": "corpus/termos/gerais.pdf",
        "revision_hash": "a" * 64,
        "page_from": 2,
        "page_to": 3,
        "excerpt": "O documento descreve as condições aplicáveis.",
        "document_date": date(2026, 1, 2),
        "was_updated": False,
        **overrides,
    }


def test_question_contract_accepts_public_global_document_scope() -> None:
    request = DocumentQuestionRequest.model_validate(
        question_payload(document_types=(DocumentType.TERMS_CONDITIONS,))
    )

    assert request.skill is DocumentSkill.DOCUMENTAL
    assert request.document_types == (DocumentType.TERMS_CONDITIONS,)
    assert request.deadline_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("property_id", str(uuid4())),
        ("property_ids", [str(uuid4())]),
        ("authorized_property_ids", [str(uuid4())]),
        ("tenant_id", str(uuid4())),
        ("access_token", "secret"),
    ],
)
def test_question_rejects_identity_or_property_scope_in_payload(field, value) -> None:
    with pytest.raises(ValidationError):
        DocumentQuestionRequest.model_validate(question_payload(**{field: value}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"question": "   "},
        {"question": "x" * 4_001},
        {"deadline_at": datetime(2026, 10, 10, 14, 0)},
        {"skill": "orchestrator"},
        {"document_types": (DocumentType.LAW,)},
        {"skill": DocumentSkill.LEGAL, "document_types": (DocumentType.TERMS_CONDITIONS,)},
        {"document_ids": ()},
        {"document_ids": tuple(uuid4() for _ in range(101))},
        {"document_types": (DocumentType.LAW, DocumentType.LAW)},
        {"reference_date": datetime(2026, 10, 10, tzinfo=UTC)},
    ],
)
def test_question_rejects_invalid_or_broad_scope(overrides) -> None:
    with pytest.raises(ValidationError):
        DocumentQuestionRequest.model_validate(question_payload(**overrides))


def test_contract_is_immutable() -> None:
    request = DocumentQuestionRequest.model_validate(question_payload())

    with pytest.raises(ValidationError):
        request.question = "alterada"


def test_source_reference_requires_hash_and_bounded_page_range() -> None:
    source = DocumentSourceReference.model_validate(source_payload())

    assert source.page_from == 2
    assert source.revision_hash == "a" * 64

    for invalid in (
        {"revision_hash": "current"},
        {"page_to": 1},
        {"page_to": 5},
        {"uploaded_at": datetime(2026, 10, 10, 14, 0)},
    ):
        with pytest.raises(ValidationError):
            DocumentSourceReference.model_validate(source_payload(**invalid))


def test_success_response_requires_a_cited_source() -> None:
    response = DocumentAnswerResponse.model_validate(
        {
            "request_id": uuid4(),
            "status": DocumentAnswerStatus.SUCCESS,
            "answer": "Estas são as condições atuais.",
            "sources": (source_payload(),),
            "corpus_revision": "b" * 64,
            "checked_at": datetime.now(UTC),
            "refresh_status": CorpusRefreshStatus.UNCHANGED,
        }
    )

    assert response.sources[0].document_type is DocumentType.TERMS_CONDITIONS
    with pytest.raises(ValidationError):
        DocumentAnswerResponse.model_validate(
            {
                "request_id": uuid4(),
                "status": DocumentAnswerStatus.SUCCESS,
                "answer": "Sem citação.",
                "refresh_status": CorpusRefreshStatus.UNCHANGED,
            }
        )


def test_unavailable_response_cannot_return_document_content() -> None:
    with pytest.raises(ValidationError):
        DocumentAnswerResponse.model_validate(
            {
                "request_id": uuid4(),
                "status": DocumentAnswerStatus.UNAVAILABLE,
                "sources": (source_payload(),),
                "limitations": ("Fonte indisponível.",),
                "refresh_status": CorpusRefreshStatus.UNAVAILABLE,
            }
        )
