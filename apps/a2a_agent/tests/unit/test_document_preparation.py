"""Extração, chunking e vetorização antes da publicação no índice."""

import sys
from datetime import date
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from argos_a2a_agent.adapters.documents import PyPdfParser
from argos_a2a_agent.application import PrepareDocumentForIndex, TextChunker
from argos_a2a_agent.domain import (
    DocumentMetadata,
    EmbeddingProviderError,
    ParsedDocument,
    ParsedPage,
    PdfExtractionError,
    SourceDocument,
)

from packages.contracts.a2a import DocumentType


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("o fake de teste não deve ceder controle de I/O")


def source_document(content: bytes = b"%PDF-synthetic") -> SourceDocument:
    metadata = DocumentMetadata(
        document_id=uuid4(),
        source_key="fixture/synthetic.pdf",
        title="Documento sintético",
        document_type=DocumentType.LAW,
        origin="fixture://global/synthetic.pdf",
        revision_hash=sha256(content).hexdigest(),
        size_bytes=len(content),
        jurisdiction="BR",
        document_date=date(2025, 1, 1),
    )
    return SourceDocument(metadata=metadata, content=content)


def install_fake_pypdf(monkeypatch, pages, *, encrypted=False):
    class FakeReader:
        def __init__(self, stream: BytesIO, *, strict: bool):
            assert stream.read().startswith(b"%PDF-")
            assert strict
            self.pages = pages
            self.is_encrypted = encrypted

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))


def test_pdf_parser_extracts_text_by_original_page_and_preserves_metadata(monkeypatch) -> None:
    document = source_document()
    install_fake_pypdf(
        monkeypatch,
        [
            SimpleNamespace(extract_text=lambda: " Lei sintética  \n artigo 1. \x00 "),
            SimpleNamespace(extract_text=lambda: "   "),
            SimpleNamespace(extract_text=lambda: "Artigo 2."),
        ],
    )

    parsed = run_immediate(PyPdfParser().parse(document.content, document.metadata))

    assert parsed.metadata == document.metadata
    assert [(page.page_number, page.text) for page in parsed.pages] == [
        (1, "Lei sintética\n artigo 1."),
        (2, ""),
        (3, "Artigo 2."),
    ]


def test_pdf_parser_rejects_wrong_hash_oversized_and_non_pdf_bytes(monkeypatch) -> None:
    document = source_document()
    parser = PyPdfParser(max_pdf_bytes=100)
    wrong_bytes = b"%PDF-different"
    with pytest.raises(PdfExtractionError, match="revisão"):
        run_immediate(parser.parse(wrong_bytes, document.metadata))
    invalid_pdf = source_document(b"not a pdf")
    with pytest.raises(PdfExtractionError, match="assinatura"):
        run_immediate(parser.parse(invalid_pdf.content, invalid_pdf.metadata))
    with pytest.raises(PdfExtractionError, match="limite de bytes"):
        run_immediate(PyPdfParser(max_pdf_bytes=1).parse(document.content, document.metadata))


def test_pdf_parser_rejects_encrypted_scanned_and_over_limit_documents(monkeypatch) -> None:
    document = source_document()
    install_fake_pypdf(monkeypatch, [SimpleNamespace(extract_text=lambda: "texto")], encrypted=True)
    with pytest.raises(PdfExtractionError, match="protegido"):
        run_immediate(PyPdfParser().parse(document.content, document.metadata))

    install_fake_pypdf(monkeypatch, [SimpleNamespace(extract_text=lambda: "   ")])
    with pytest.raises(PdfExtractionError, match="OCR"):
        run_immediate(PyPdfParser().parse(document.content, document.metadata))

    install_fake_pypdf(
        monkeypatch,
        [SimpleNamespace(extract_text=lambda: "texto") for _ in range(3)],
    )
    with pytest.raises(PdfExtractionError, match="páginas"):
        run_immediate(PyPdfParser(max_pages=2).parse(document.content, document.metadata))


def parsed_document(*, text: str, page_number: int = 1) -> ParsedDocument:
    metadata = source_document().metadata
    return ParsedDocument(metadata=metadata, pages=(ParsedPage(page_number, text),))


def test_chunker_respects_page_boundaries_and_creates_stable_revision_ids() -> None:
    metadata = source_document().metadata
    document = ParsedDocument(
        metadata=metadata,
        pages=(ParsedPage(1, "cláusula " * 100), ParsedPage(2, "artigo " * 20)),
    )
    chunker = TextChunker(max_chars=300, overlap_chars=40)

    first = chunker.split(document)
    second = chunker.split(document)

    assert first == second
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))
    assert all(len(chunk.text) <= 300 for chunk in first)
    assert {chunk.page_from for chunk in first} == {1, 2}
    assert all(chunk.page_from == chunk.page_to for chunk in first)


def test_chunker_rejects_empty_text_and_chunk_count_over_limit() -> None:
    with pytest.raises(PdfExtractionError, match="não produziu"):
        TextChunker().split(parsed_document(text=" \n "))
    with pytest.raises(PdfExtractionError, match="limite de chunks"):
        TextChunker(max_chars=100, overlap_chars=0, max_chunks=1).split(
            parsed_document(text="palavra " * 100)
        )


class FakeParser:
    def __init__(self, *, mismatched_metadata=False):
        self.mismatched_metadata = mismatched_metadata

    async def parse(self, content, metadata):
        if self.mismatched_metadata:
            metadata = source_document(b"another revision").metadata
        return ParsedDocument(metadata, (ParsedPage(1, "cláusula " * 30),))


class FakeEmbeddings:
    def __init__(self, vectors=None):
        self.vectors = vectors
        self.calls = []

    async def embed(self, texts, *, input_type="document"):
        assert input_type == "document"
        self.calls.append(tuple(texts))
        if self.vectors is not None:
            return self.vectors(len(texts), len(self.calls))
        return [(float(index), 0.25) for index in range(len(texts))]


def test_preparation_batches_embeddings_and_returns_chunks_without_writing_index() -> None:
    source = source_document()
    embeddings = FakeEmbeddings()
    use_case = PrepareDocumentForIndex(
        FakeParser(),
        embeddings,
        TextChunker(max_chars=100, overlap_chars=10),
        embedding_batch_size=2,
    )

    indexed = run_immediate(use_case.execute(source))

    assert len(indexed) > 2
    assert len(embeddings.calls) == (len(indexed) + 1) // 2
    assert [item.chunk for item in indexed]
    assert all(item.chunk.document_id == source.metadata.document_id for item in indexed)
    assert all(item.chunk.revision_hash == source.metadata.revision_hash for item in indexed)
    assert all(len(item.vector) == 2 for item in indexed)


def test_preparation_indexes_each_chunk_in_openai_and_voyage_spaces() -> None:
    source = source_document()

    class VoyageEmbeddings:
        async def embed(self, texts, *, input_type="document"):
            assert input_type == "document"
            return tuple((0.5, 0.25, 0.1, 0.05) for _ in texts)

    use_case = PrepareDocumentForIndex(
        FakeParser(),
        FakeEmbeddings(),
        TextChunker(max_chars=100, overlap_chars=10),
        embedding_batch_size=2,
        voyage_embeddings=VoyageEmbeddings(),
    )

    indexed = run_immediate(use_case.execute(source))

    assert all(item.voyage_vector == (0.5, 0.25, 0.1, 0.05) for item in indexed)


@pytest.mark.parametrize(
    "vector_factory",
    [
        lambda count, call: [],
        lambda count, call: [(float("nan"),)] * count,
        lambda count, call: [()] * count,
        lambda count, call: [(1.0,), (1.0, 2.0)] if count == 2 else [],
    ],
)
def test_preparation_rejects_invalid_embedding_batches(vector_factory) -> None:
    source = source_document()
    use_case = PrepareDocumentForIndex(
        FakeParser(),
        FakeEmbeddings(vector_factory),
        TextChunker(max_chars=100, overlap_chars=10),
        embedding_batch_size=2,
    )

    with pytest.raises(EmbeddingProviderError):
        run_immediate(use_case.execute(source))


def test_preparation_rejects_embedding_dimension_changes_between_batches() -> None:
    source = source_document()

    def changing_dimensions(count, call):
        return [(1.0, 2.0) if call == 1 else (1.0,)] * count

    use_case = PrepareDocumentForIndex(
        FakeParser(),
        FakeEmbeddings(changing_dimensions),
        TextChunker(max_chars=100, overlap_chars=10),
        embedding_batch_size=1,
    )

    with pytest.raises(EmbeddingProviderError, match="vetores inválidos"):
        run_immediate(use_case.execute(source))


def test_preparation_rejects_parser_metadata_for_another_revision() -> None:
    source = source_document()
    use_case = PrepareDocumentForIndex(FakeParser(mismatched_metadata=True), FakeEmbeddings())

    with pytest.raises(PdfExtractionError, match="outra revisão"):
        run_immediate(use_case.execute(source))
