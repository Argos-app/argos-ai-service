from datetime import UTC, datetime
from uuid import uuid4

import pytest
from argos_a2a_agent.application.document_queries import SearchDocuments
from argos_a2a_agent.application.ensure_current_corpus import EnsureCurrentCorpusOutcome
from argos_a2a_agent.domain.models import (
    DocumentChunk,
    DocumentFilter,
    DocumentMetadata,
    DocumentPage,
    DocumentSearchHit,
)

from packages.contracts.a2a import CorpusRefreshStatus, DocumentType
from packages.contracts.documents import SearchDocumentsRequest

CORPUS_REVISION = "a" * 64


def _metadata(title: str, document_type: DocumentType) -> DocumentMetadata:
    document_id = uuid4()
    return DocumentMetadata(
        document_id=document_id,
        source_key=f"{document_id}.pdf",
        title=title,
        document_type=document_type,
        origin=f"fixture://{document_id}.pdf",
        revision_hash=CORPUS_REVISION,
        size_bytes=1,
    )


class _EnsureCurrent:
    async def execute(self, context):
        del context
        return EnsureCurrentCorpusOutcome(
            status=CorpusRefreshStatus.UNCHANGED,
            checked_at=datetime.now(UTC),
            corpus_revision=CORPUS_REVISION,
        )

    async def reauthorize(self, context):
        del context


class _Repository:
    def __init__(self, documents):
        self.documents = tuple(documents)
        self.search_filters = None

    def _filtered(self, filters: DocumentFilter):
        selected = self.documents
        if filters.document_ids is not None:
            selected = tuple(item for item in selected if item.document_id in filters.document_ids)
        if filters.document_types is not None:
            selected = tuple(
                item for item in selected if item.document_type in filters.document_types
            )
        return selected

    async def count_documents(self, *, filters):
        return len(self._filtered(filters))

    async def list_documents(self, *, filters, page_size, cursor):
        del cursor
        documents = self._filtered(filters)[:page_size]
        return DocumentPage(documents=documents, next_cursor=None, complete=True)

    async def search(self, vector, *, filters, limit):
        del vector
        self.search_filters = filters
        selected = self._filtered(filters)
        # Simulate a semantic index that ranks terms above a law for this query.
        document = next(
            (item for item in selected if item.document_type == DocumentType.TERMS_CONDITIONS),
            selected[0],
        )
        return (
            DocumentSearchHit(
                chunk=DocumentChunk(
                    chunk_id=uuid4(),
                    document_id=document.document_id,
                    revision_hash=document.revision_hash,
                    chunk_index=0,
                    page_from=1,
                    page_to=1,
                    text=f"Trecho sintético de {document.title}.",
                ),
                score=0.9,
            ),
        )[:limit]


class _Embeddings:
    async def embed(self, texts):
        return ((0.1, 0.2),) if len(texts) == 1 else ()


@pytest.mark.asyncio
async def test_explicit_document_title_limits_semantic_search_to_that_document():
    law = _metadata("Lei Sintética Global de Demonstração", DocumentType.LAW)
    terms = _metadata("Termos e Condições de Uso", DocumentType.TERMS_CONDITIONS)
    repository = _Repository((law, terms))
    query = SearchDocuments(_EnsureCurrent(), repository, _Embeddings())

    response = await query.execute(
        SearchDocumentsRequest(query="Explique a Lei sintética global de demonstração."),
        context=None,
    )

    assert response.hits[0].document_id == law.document_id
    assert repository.search_filters is not None
    assert repository.search_filters.document_ids == (law.document_id,)
