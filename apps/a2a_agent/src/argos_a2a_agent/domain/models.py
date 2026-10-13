"""Tipos internos do corpus documental, sem dependência do transporte A2A."""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from uuid import UUID

from packages.contracts.a2a import DocumentType, LegalValidityStatus
from packages.contracts.a2a import RevisionHash as _RevisionHash

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
RevisionHash = _RevisionHash


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} deve ser SHA-256 hexadecimal em minúsculas")


def _require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} deve incluir timezone")


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """Metadados allowlisted de uma lei ou de termos globais."""

    document_id: UUID
    source_key: str
    title: str
    document_type: DocumentType
    origin: str
    revision_hash: RevisionHash
    size_bytes: int
    source_etag: str | None = None
    source_modified_at: datetime | None = None
    jurisdiction: str | None = None
    document_date: date | None = None
    uploaded_at: datetime | None = None
    updated_at: datetime | None = None
    legal_validity: LegalValidityStatus = LegalValidityStatus.UNKNOWN

    def __post_init__(self) -> None:
        if not self.source_key.strip() or not self.title.strip() or not self.origin.strip():
            raise ValueError("source_key, title e origin não podem ser vazios")
        if len(self.title) > 200 or len(self.origin) > 500:
            raise ValueError("title ou origin excede o limite de tamanho")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise ValueError("size_bytes deve ser inteiro")
        if self.size_bytes <= 0:
            raise ValueError("size_bytes deve ser maior que zero")
        if not isinstance(self.document_type, DocumentType):
            raise ValueError("document_type deve ser law ou terms_conditions")
        if not isinstance(self.legal_validity, LegalValidityStatus):
            raise ValueError("legal_validity inválida")
        if self.jurisdiction is not None and (
            not self.jurisdiction.strip() or len(self.jurisdiction) > 120
        ):
            raise ValueError("jurisdiction inválida")
        _require_sha256(self.revision_hash, "revision_hash")
        _require_aware(self.source_modified_at, "source_modified_at")
        _require_aware(self.uploaded_at, "uploaded_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Bytes de PDF obtidos da fonte allowlisted e seus metadados verificados."""

    metadata: DocumentMetadata
    content: bytes

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("o PDF não pode estar vazio")
        if len(self.content) != self.metadata.size_bytes:
            raise ValueError("tamanho do PDF difere dos metadados da fonte")
        if sha256(self.content).hexdigest() != self.metadata.revision_hash:
            raise ValueError("hash do PDF difere da revisão declarada pela fonte")


@dataclass(frozen=True, slots=True)
class SourceDocumentListing:
    """Página de listagem com completude explícita para evitar exclusão indevida."""

    documents: tuple[DocumentMetadata, ...]
    complete: bool
    continuation_cursor: str | None = None

    def __post_init__(self) -> None:
        ids = [document.document_id for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("a listagem da fonte não pode repetir documentos")
        if self.complete and self.continuation_cursor is not None:
            raise ValueError("listagem completa não pode ter cursor de continuação")


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    """Snapshot técnico do conjunto global atualmente publicado."""

    corpus_revision: RevisionHash
    documents: tuple[DocumentMetadata, ...]
    generated_at: datetime

    def __post_init__(self) -> None:
        _require_sha256(self.corpus_revision, "corpus_revision")
        _require_aware(self.generated_at, "generated_at")
        ids = [document.document_id for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("manifesto não pode repetir documentos")


@dataclass(frozen=True, slots=True)
class ParsedPage:
    page_number: int
    text: str

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number deve ser positivo")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    metadata: DocumentMetadata
    pages: tuple[ParsedPage, ...]

    def __post_init__(self) -> None:
        numbers = [page.page_number for page in self.pages]
        if not numbers or numbers != sorted(set(numbers)):
            raise ValueError("páginas devem ser únicas e estar em ordem crescente")


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: UUID
    document_id: UUID
    revision_hash: RevisionHash
    chunk_index: int
    page_from: int
    page_to: int
    text: str

    def __post_init__(self) -> None:
        _require_sha256(self.revision_hash, "revision_hash")
        if self.chunk_index < 0 or self.page_from < 1 or self.page_to < self.page_from:
            raise ValueError("índice ou intervalo de páginas do chunk inválido")
        if not self.text.strip():
            raise ValueError("chunk não pode ser vazio")


@dataclass(frozen=True, slots=True)
class IndexedDocumentChunk:
    chunk: DocumentChunk
    vector: tuple[float, ...]
    metadata: DocumentMetadata

    def __post_init__(self) -> None:
        if (
            self.metadata.document_id != self.chunk.document_id
            or self.metadata.revision_hash != self.chunk.revision_hash
        ):
            raise ValueError("metadados do chunk devem corresponder ao documento e à revisão")
        if not self.vector or any(not math.isfinite(value) for value in self.vector):
            raise ValueError("vetor deve conter valores finitos")


@dataclass(frozen=True, slots=True)
class DocumentFilter:
    """Filtro interno normalizado; nunca aceita expressão Qdrant do agente."""

    document_ids: tuple[UUID, ...] | None = None
    document_types: tuple[DocumentType, ...] | None = None
    jurisdictions: tuple[str, ...] | None = None
    document_date_from: date | None = None
    document_date_to: date | None = None
    corpus_revision: RevisionHash | None = None

    def __post_init__(self) -> None:
        for values, name in (
            (self.document_ids, "document_ids"),
            (self.document_types, "document_types"),
            (self.jurisdictions, "jurisdictions"),
        ):
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{name} não pode conter duplicatas")
            if values is not None and not values:
                raise ValueError(f"{name} não pode ser vazio quando informado")
        if self.document_ids is not None and len(self.document_ids) > 100:
            raise ValueError("document_ids excede o limite de 100")
        if self.document_types is not None and (
            len(self.document_types) > 2
            or any(not isinstance(value, DocumentType) for value in self.document_types)
        ):
            raise ValueError("document_types deve conter tipos documentais permitidos")
        if self.jurisdictions is not None and len(self.jurisdictions) > 20:
            raise ValueError("jurisdictions excede o limite de 20")
        if self.jurisdictions is not None and any(
            not value.strip() for value in self.jurisdictions
        ):
            raise ValueError("jurisdictions não pode conter valores vazios")
        if (
            self.document_date_from is not None
            and self.document_date_to is not None
            and self.document_date_from > self.document_date_to
        ):
            raise ValueError("intervalo de datas inválido")
        if self.corpus_revision is not None:
            _require_sha256(self.corpus_revision, "corpus_revision")


@dataclass(frozen=True, slots=True)
class DocumentSearchHit:
    chunk: DocumentChunk
    score: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.score):
            raise ValueError("score deve ser finito")


@dataclass(frozen=True, slots=True)
class DocumentPage:
    documents: tuple[DocumentMetadata, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True, slots=True)
class CorpusSyncResult:
    corpus_revision: RevisionHash
    changed_document_ids: tuple[UUID, ...]
    removed_document_ids: tuple[UUID, ...]
    checked_at: datetime
    updated: bool

    def __post_init__(self) -> None:
        _require_sha256(self.corpus_revision, "corpus_revision")
        _require_aware(self.checked_at, "checked_at")
        if len(self.changed_document_ids) != len(set(self.changed_document_ids)):
            raise ValueError("changed_document_ids não pode conter duplicatas")
        if len(self.removed_document_ids) != len(set(self.removed_document_ids)):
            raise ValueError("removed_document_ids não pode conter duplicatas")
        if set(self.changed_document_ids) & set(self.removed_document_ids):
            raise ValueError("um documento não pode estar alterado e removido na mesma execução")
        if not self.updated and (self.changed_document_ids or self.removed_document_ids):
            raise ValueError("resultado sem atualização não pode indicar mudanças")
