"""Testes de filtros fixos e limites das consultas documentais read-only."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.a2a import DocumentType
from packages.contracts.documents import (
    AggregateDocumentsRequest,
    DocumentAvailability,
    DocumentGroupDimension,
    GetDocumentExcerptRequest,
    ListDocumentsRequest,
    SearchDocumentsRequest,
)


def test_search_request_accepts_only_fixed_global_filters() -> None:
    request = SearchDocumentsRequest.model_validate(
        {
            "query": "  cláusula de vigência  ",
            "document_types": (DocumentType.LAW,),
            "jurisdictions": ("BR",),
            "document_date_from": date(2020, 1, 1),
            "document_date_to": date(2026, 10, 10),
            "top_k": 10,
        }
    )

    assert request.query == "cláusula de vigência"
    assert request.top_k == 10


@pytest.mark.parametrize(
    "extra",
    [
        {"property_ids": (str(uuid4()),)},
        {"collection_name": "another_collection"},
        {"filter": {"must": []}},
        {"sql": "SELECT * FROM documents"},
    ],
)
def test_search_rejects_agent_selected_scope_or_query_language(extra) -> None:
    with pytest.raises(ValidationError):
        SearchDocumentsRequest.model_validate({"query": "lei vigente", **extra})


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "lei", "top_k": 0},
        {"query": "lei", "top_k": 21},
        {"query": "lei", "document_date_from": "2027-01-01", "document_date_to": "2026-01-01"},
        {"query": "lei", "document_types": ()},
        {"query": "lei", "document_types": (DocumentType.LAW, DocumentType.LAW)},
        {"query": "lei", "jurisdictions": ("BR", "BR")},
        {"query": "lei", "limit": 10_000},
    ],
)
def test_search_rejects_invalid_or_unbounded_filters(payload) -> None:
    with pytest.raises(ValidationError):
        SearchDocumentsRequest.model_validate(payload)


def test_excerpt_request_bounds_document_pages_and_revision() -> None:
    document_id = uuid4()
    request = GetDocumentExcerptRequest.model_validate(
        {
            "document_id": document_id,
            "page_from": 4,
            "page_to": 6,
            "expected_revision": "c" * 64,
        }
    )

    assert request.document_id == document_id
    for invalid in (
        {"page_from": 0, "page_to": 2},
        {"page_from": 4, "page_to": 3},
        {"page_from": 1, "page_to": 4},
        {"page_from": 1, "page_to": 1, "expected_revision": "old"},
    ):
        with pytest.raises(ValidationError):
            GetDocumentExcerptRequest.model_validate({"document_id": document_id, **invalid})


def test_list_request_caps_page_size_and_cursor_length() -> None:
    assert ListDocumentsRequest.model_validate({"page_size": 50}).page_size == 50

    for invalid in ({"page_size": 51}, {"page_size": 0}, {"cursor": "x" * 2_049}):
        with pytest.raises(ValidationError):
            ListDocumentsRequest.model_validate(invalid)


def test_aggregation_dimensions_are_allowlisted_and_unique() -> None:
    request = AggregateDocumentsRequest.model_validate(
        {
            "document_types": (DocumentType.LAW,),
            "group_by": (
                DocumentGroupDimension.DOCUMENT_TYPE,
                DocumentGroupDimension.JURISDICTION,
            ),
        }
    )

    assert request.group_by == (
        DocumentGroupDimension.DOCUMENT_TYPE,
        DocumentGroupDimension.JURISDICTION,
    )
    for invalid in (
        {"group_by": ()},
        {"group_by": ("property_id",)},
        {"group_by": ("document_year", "document_year")},
        {"group_by": ("document_year", "availability", "jurisdiction")},
    ):
        with pytest.raises(ValidationError):
            AggregateDocumentsRequest.model_validate(invalid)


def test_availability_is_a_closed_enum() -> None:
    assert DocumentAvailability.READY.value == "ready"
    with pytest.raises(ValidationError):
        SearchDocumentsRequest.model_validate({"query": "lei", "availability": "deleted"})
