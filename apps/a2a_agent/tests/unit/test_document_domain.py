"""Invariantes dos modelos globais e fronteiras de leitura/escrita do A2A."""

from dataclasses import fields
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from argos_a2a_agent.domain import (
    CorpusManifest,
    CorpusSyncResult,
    DocumentChunk,
    DocumentFilter,
    DocumentMetadata,
    DocumentSearchHit,
    IndexedDocumentChunk,
    ParsedDocument,
    ParsedPage,
    SourceDocument,
    SourceDocumentListing,
)
from argos_a2a_agent.ports import DocumentIndexWriter, DocumentSource, QdrantReader

from packages.contracts.a2a import DocumentType


def metadata(
    *,
    document_id=None,
    content: bytes = b"synthetic pdf bytes",
    document_type: DocumentType = DocumentType.LAW,
) -> DocumentMetadata:
    return DocumentMetadata(
        document_id=document_id or uuid4(),
        source_key="fixture/law-001.pdf",
        title="Lei sintética 001",
        document_type=document_type,
        origin="fixture://global/law-001.pdf",
        revision_hash=sha256(content).hexdigest(),
        size_bytes=len(content),
        jurisdiction="BR-SP",
        document_date=date(2025, 1, 1),
    )


def chunk(*, revision_hash: str | None = None) -> DocumentChunk:
    source = metadata()
    return DocumentChunk(
        chunk_id=uuid4(),
        document_id=source.document_id,
        revision_hash=revision_hash or source.revision_hash,
        chunk_index=0,
        page_from=1,
        page_to=2,
        text="Texto sintético da lei.",
    )


def test_source_document_matches_declared_size_and_sha256() -> None:
    source_metadata = metadata()

    source = SourceDocument(metadata=source_metadata, content=b"synthetic pdf bytes")

    assert source.metadata == source_metadata

    with pytest.raises(ValueError, match="tamanho"):
        SourceDocument(metadata=source_metadata, content=b"partial")

    wrong_hash_metadata = DocumentMetadata(
        **{
            field.name: getattr(source_metadata, field.name)
            for field in fields(source_metadata)
            if field.name != "revision_hash"
        },
        revision_hash="a" * 64,
    )
    with pytest.raises(ValueError, match="hash"):
        SourceDocument(metadata=wrong_hash_metadata, content=b"synthetic pdf bytes")


@pytest.mark.parametrize(
    "changes",
    [
        {"revision_hash": "not-a-sha256"},
        {"size_bytes": 0},
        {"document_type": "external"},
        {"title": " "},
        {"jurisdiction": " "},
    ],
)
def test_document_metadata_rejects_invalid_or_unclassified_documents(changes) -> None:
    source_metadata = metadata()
    values = {
        field.name: getattr(source_metadata, field.name) for field in fields(DocumentMetadata)
    }

    with pytest.raises(ValueError):
        DocumentMetadata(**(values | changes))


def test_source_listing_marks_incomplete_enumeration_without_claiming_deletions() -> None:
    first = metadata()
    listing = SourceDocumentListing(
        documents=(first,), complete=False, continuation_cursor="next-page"
    )

    assert not listing.complete
    assert listing.continuation_cursor == "next-page"

    with pytest.raises(ValueError, match="completa"):
        SourceDocumentListing(documents=(first,), complete=True, continuation_cursor="next-page")
    with pytest.raises(ValueError, match="repetir"):
        SourceDocumentListing(documents=(first, first), complete=False)


def test_manifest_requires_unique_document_ids_and_sha256_generation() -> None:
    document = metadata()
    manifest = CorpusManifest(
        corpus_revision="b" * 64,
        documents=(document,),
        generated_at=datetime.now(UTC),
    )

    assert manifest.documents == (document,)
    with pytest.raises(ValueError, match="repetir"):
        CorpusManifest(
            corpus_revision="b" * 64,
            documents=(document, document),
            generated_at=datetime.now(UTC),
        )
    with pytest.raises(ValueError, match="SHA-256"):
        CorpusManifest(
            corpus_revision="invalid",
            documents=(),
            generated_at=datetime.now(UTC),
        )


def test_parsed_pages_are_nonempty_ordered_and_unique() -> None:
    document = metadata()
    parsed = ParsedDocument(
        metadata=document,
        pages=(ParsedPage(page_number=1, text="Página um"), ParsedPage(2, "Página dois")),
    )

    assert [page.page_number for page in parsed.pages] == [1, 2]
    for pages in ((), (ParsedPage(2, "dois"), ParsedPage(1, "um")), (ParsedPage(1, "a"),) * 2):
        with pytest.raises(ValueError, match="páginas"):
            ParsedDocument(metadata=document, pages=pages)
    with pytest.raises(ValueError, match="positivo"):
        ParsedPage(page_number=0, text="inválida")


def test_chunks_and_embeddings_require_current_sha_and_finite_vectors() -> None:
    document_chunk = chunk()
    source_metadata = metadata(document_id=document_chunk.document_id)
    indexed = IndexedDocumentChunk(
        chunk=document_chunk, vector=(0.1, 0.2), metadata=source_metadata
    )

    assert indexed.vector == (0.1, 0.2)
    with pytest.raises(ValueError, match="SHA-256"):
        chunk(revision_hash="old")
    with pytest.raises(ValueError, match="vazio"):
        DocumentChunk(uuid4(), uuid4(), "c" * 64, 0, 1, 1, "  ")
    with pytest.raises(ValueError, match="finitos"):
        IndexedDocumentChunk(
            chunk=document_chunk,
            vector=(float("nan"),),
            metadata=source_metadata,
        )
    with pytest.raises(ValueError, match="score"):
        DocumentSearchHit(chunk=document_chunk, score=float("inf"))


def test_document_filters_are_bounded_unique_and_date_ordered() -> None:
    document_id = uuid4()
    filters = DocumentFilter(
        document_ids=(document_id,),
        document_types=(DocumentType.TERMS_CONDITIONS,),
        jurisdictions=("BR-SP",),
        document_date_from=date(2024, 1, 1),
        document_date_to=date(2025, 1, 1),
        corpus_revision="d" * 64,
    )

    assert filters.document_types == (DocumentType.TERMS_CONDITIONS,)
    with pytest.raises(ValueError, match="duplicatas"):
        DocumentFilter(document_ids=(document_id, document_id))
    with pytest.raises(ValueError, match="vazio"):
        DocumentFilter(document_types=())
    with pytest.raises(ValueError, match="limite de 100"):
        DocumentFilter(document_ids=tuple(uuid4() for _ in range(101)))
    with pytest.raises(ValueError, match="datas"):
        DocumentFilter(document_date_from=date(2026, 1, 1), document_date_to=date(2025, 1, 1))


def test_sync_result_requires_a_timezone_aware_check_time() -> None:
    result = CorpusSyncResult(
        corpus_revision="e" * 64,
        changed_document_ids=(uuid4(),),
        removed_document_ids=(),
        checked_at=datetime.now(UTC),
        updated=True,
    )

    assert result.updated
    with pytest.raises(ValueError, match="timezone"):
        CorpusSyncResult("e" * 64, (), (), datetime.now(), False)
    changed_id = result.changed_document_ids[0]
    with pytest.raises(ValueError, match="alterado e removido"):
        CorpusSyncResult("e" * 64, (changed_id,), (changed_id,), datetime.now(UTC), True)


def test_source_and_reader_ports_cannot_write_while_index_writer_is_separate() -> None:
    assert set(DocumentSource.__dict__) >= {"list_documents", "read_pdf"}
    assert not any("write" in name or "delete" in name for name in DocumentSource.__dict__)
    assert set(QdrantReader.__dict__) >= {"query_points", "retrieve", "count"}
    assert not any("upsert" in name or "delete" in name for name in QdrantReader.__dict__)
    assert set(DocumentIndexWriter.__dict__) >= {
        "upsert_chunks",
        "remove_document",
        "publish",
        "discard",
    }
