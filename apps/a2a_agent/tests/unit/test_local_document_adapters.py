"""Allowlist de PDFs locais e persistência do manifesto técnico."""

import json
import unicodedata
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from argos_a2a_agent.adapters.documents import PyPdfParser
from argos_a2a_agent.adapters.storage import LocalDocumentSource, LocalManifestRepository
from argos_a2a_agent.domain import (
    CorpusManifest,
    DocumentMetadata,
    ManifestStoreError,
    SourceReadError,
)

from packages.contracts.a2a import DocumentType, LegalValidityStatus

FIXTURES = Path(__file__).parents[2] / "data" / "documents"


def run_immediate(coroutine):
    """Executa corrotinas dos adapters locais sem iniciar o event loop do Windows."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("o adapter local não deve ceder controle de I/O")


def copy_catalog(tmp_path: Path) -> list[dict]:
    documents = json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))["documents"]
    for item in documents:
        (tmp_path / item["file_name"]).write_bytes((FIXTURES / item["file_name"]).read_bytes())
    (tmp_path / "catalog.json").write_text(json.dumps({"documents": documents}), encoding="utf-8")
    return documents


def test_local_source_lists_and_reads_only_global_laws_and_terms() -> None:
    source = LocalDocumentSource(FIXTURES)

    listing = run_immediate(source.list_documents())

    assert listing.complete
    assert len(listing.documents) == 2
    by_type = {item.document_type: item for item in listing.documents}
    assert set(by_type) == {DocumentType.LAW, DocumentType.TERMS_CONDITIONS}
    assert by_type[DocumentType.LAW].document_date == date(2025, 1, 15)
    assert by_type[DocumentType.LAW].jurisdiction == "BR"
    assert by_type[DocumentType.TERMS_CONDITIONS].jurisdiction is None
    for item in listing.documents:
        content = (FIXTURES / item.source_key).read_bytes()
        assert item.revision_hash == sha256(content).hexdigest()
        assert item.size_bytes == len(content)


def test_local_source_reads_pdf_and_does_not_mutate_fixture_files() -> None:
    before = {path.name: path.read_bytes() for path in FIXTURES.iterdir() if path.is_file()}
    source = LocalDocumentSource(FIXTURES)
    document_id = UUID("11111111-1111-4111-8111-111111111111")

    source_document = run_immediate(source.read_pdf(document_id))

    assert source_document is not None
    assert source_document.content.startswith(b"%PDF-")
    assert source_document.metadata.document_type is DocumentType.LAW
    assert {path.name: path.read_bytes() for path in FIXTURES.iterdir() if path.is_file()} == before
    assert run_immediate(source.read_pdf(uuid4())) is None


def test_bundled_demo_pdfs_are_extractable_and_disclaim_real_legal_authority() -> None:
    source = LocalDocumentSource(FIXTURES)
    parser = PyPdfParser()
    documents = run_immediate(source.list_documents()).documents

    extracted = [
        run_immediate(parser.parse(run_immediate(source.read_pdf(item.document_id)).content, item))
        for item in documents
    ]
    text = "\n".join(page.text for document in extracted for page in document.pages)
    normalized_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    normalized_text = normalized_text.upper()

    assert "SINTETICO" in normalized_text
    assert "NAO E LEGISLACAO REAL" in normalized_text
    assert "NAO E CONTRATO REAL" in normalized_text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_type", "external"),
        ("file_name", "../outside.pdf"),
        ("document_date", "15/01/2025"),
        ("document_id", "not-a-uuid"),
    ],
)
def test_local_source_rejects_invalid_classification_identity_date_and_path(
    tmp_path: Path, field: str, value: str
) -> None:
    documents = copy_catalog(tmp_path)
    documents[0][field] = value
    (tmp_path / "catalog.json").write_text(json.dumps({"documents": documents}), encoding="utf-8")

    with pytest.raises(SourceReadError):
        run_immediate(LocalDocumentSource(tmp_path).list_documents())


def test_local_source_rejects_partial_or_duplicate_catalog_and_unsupported_cursor(
    tmp_path: Path,
) -> None:
    documents = copy_catalog(tmp_path)
    documents.append(documents[0])
    (tmp_path / "catalog.json").write_text(json.dumps({"documents": documents}), encoding="utf-8")
    source = LocalDocumentSource(tmp_path)
    with pytest.raises(SourceReadError):
        run_immediate(source.list_documents())
    with pytest.raises(SourceReadError):
        run_immediate(source.list_documents(cursor="arbitrary"))


def test_local_source_rejects_pdf_outside_allowlisted_root(tmp_path: Path) -> None:
    documents = copy_catalog(tmp_path)
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\nsynthetic outside\n")
    documents[0]["file_name"] = str(outside)
    (tmp_path / "catalog.json").write_text(json.dumps({"documents": documents}), encoding="utf-8")

    with pytest.raises(SourceReadError):
        run_immediate(LocalDocumentSource(tmp_path).list_documents())


def test_manifest_repository_round_trips_global_metadata_and_writes_separate_state(
    tmp_path: Path,
) -> None:
    source = run_immediate(LocalDocumentSource(FIXTURES).list_documents())
    manifest = CorpusManifest(
        corpus_revision="a" * 64,
        documents=source.documents,
        generated_at=datetime(2025, 2, 3, tzinfo=UTC),
    )
    source_snapshot = {
        path.name: path.read_bytes() for path in FIXTURES.iterdir() if path.is_file()
    }
    repository = LocalManifestRepository(tmp_path / "state" / "manifest.json")

    assert run_immediate(repository.load()) is None
    run_immediate(repository.save(manifest))

    assert run_immediate(repository.load()) == manifest
    assert {
        path.name: path.read_bytes() for path in FIXTURES.iterdir() if path.is_file()
    } == source_snapshot


def test_manifest_repository_rejects_corrupt_or_unclassified_state(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version": 1, "documents": []}', encoding="utf-8")

    with pytest.raises(ManifestStoreError):
        run_immediate(LocalManifestRepository(path).load())


def test_manifest_domain_metadata_remains_limited_to_global_document_types() -> None:
    content = b"synthetic"
    with pytest.raises(ValueError, match="law ou terms_conditions"):
        DocumentMetadata(
            document_id=uuid4(),
            source_key="fixture.pdf",
            title="Documento sintético",
            document_type="external",
            origin="fixture://global/fixture.pdf",
            revision_hash=sha256(content).hexdigest(),
            size_bytes=len(content),
            legal_validity=LegalValidityStatus.UNKNOWN,
        )
