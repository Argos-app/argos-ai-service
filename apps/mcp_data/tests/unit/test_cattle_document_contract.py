"""Contratos de integridade e escopo para os documentos Mongo bovinos."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.domain.cattle_documents import (
    CattleHistoryDocument,
    ListCattleDocumentsRequest,
)
from argos_mcp_data.infrastructure.cattle_document_repository import (
    MongoCattleDocumentRepository,
)


def _document(*, summary=None):
    document = {
        "_id": "mongo-id",
        "relational_property_id": str(uuid4()),
        "herd": {"herd_id": str(uuid4())},
        "lots": [],
    }
    if summary is not None:
        document["history_summary"] = summary
    return document


def test_cattle_document_maps_mongo_id_and_rejects_summary_that_signals_truncation():
    document = CattleHistoryDocument.model_validate(_document(summary={"total_lots": 0}))
    assert document.id == "mongo-id"

    with pytest.raises(ValueError, match="inválido ou inconsistente"):
        CattleHistoryDocument.model_validate(_document(summary={"total_lots": 2}))


def test_cattle_document_request_rejects_naive_update_dates():
    with pytest.raises(ValueError, match="fuso horário"):
        ListCattleDocumentsRequest(updated_from=datetime(2026, 1, 1))


def test_mongo_query_uses_only_revalidated_scope_as_property_ceiling():
    property_a, property_b, property_outside = uuid4(), uuid4(), uuid4()
    scope = AuthorizedScope(
        user_id=uuid4(),
        property_ids=(property_a,),
        operation="cattle.read",
        agent_id="fazenda",
        token_id="token",
        request_id="request",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    repository = MongoCattleDocumentRepository(object(), "bois", "string")
    query = repository._build_filter(
        scope,
        ListCattleDocumentsRequest(property_ids=(property_a,), herd_id=uuid4()),
    )

    assert query["relational_property_id"] == {"$in": [str(property_a)]}
    assert str(property_b) not in str(query)
    assert str(property_outside) not in str(query)
    assert "herd.herd_id" in query
