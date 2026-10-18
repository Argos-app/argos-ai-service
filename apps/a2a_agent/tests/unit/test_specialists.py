"""Filtros próprios dos especialistas documental e jurídico."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argos_a2a_agent.agents.specialists import DocumentalAgent, LegalAgent

from packages.contracts.a2a import DocumentQuestionRequest, DocumentSkill


def test_mixed_documental_search_keeps_global_terms_without_jurisdiction_filter():
    request = DocumentQuestionRequest(
        request_id=uuid4(),
        question="Compare termos e legislação",
        skill=DocumentSkill.MIXED,
        jurisdiction="BR",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    agent = DocumentalAgent(None, None, None, None)

    search_request = agent._search_request(request)

    assert search_request.document_types == (agent.document_type,)
    assert search_request.jurisdictions is None


def test_legal_search_filters_by_requested_jurisdiction():
    request = DocumentQuestionRequest(
        request_id=uuid4(),
        question="Consulte a legislação brasileira",
        skill=DocumentSkill.LEGAL,
        jurisdiction="BR",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    agent = LegalAgent(None, None, None, None)

    search_request = agent._search_request(request)

    assert search_request.jurisdictions == ("BR",)
