"""Contratos de somente leitura das consultas ao corpus global de leis e termos."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from .a2a import (
    DocumentIdFilter,
    DocumentSourceReference,
    DocumentType,
    RevisionHash,
    _calendar_date,
    _unique_values,
)


class _DocumentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DocumentAvailability(StrEnum):
    READY = "ready"
    UPDATING = "updating"
    UNAVAILABLE = "unavailable"


class DocumentGroupDimension(StrEnum):
    DOCUMENT_TYPE = "document_type"
    JURISDICTION = "jurisdiction"
    DOCUMENT_YEAR = "document_year"
    AVAILABILITY = "availability"


JurisdictionFilter = Annotated[tuple[str, ...], Field(min_length=1, max_length=20)]
DocumentTypeFilter = Annotated[tuple[DocumentType, ...], Field(min_length=1, max_length=2)]
PositivePage = Annotated[StrictInt, Field(ge=1)]
PageSize = Annotated[StrictInt, Field(ge=1, le=50)]
TopK = Annotated[StrictInt, Field(ge=1, le=20)]
BoundedCursor = Annotated[str, Field(min_length=1, max_length=2_048)]


class _DocumentFilter(_DocumentContract):
    document_ids: DocumentIdFilter | None = None
    document_types: DocumentTypeFilter | None = None
    jurisdictions: JurisdictionFilter | None = None
    document_date_from: date | None = None
    document_date_to: date | None = None

    @field_validator("document_ids", "document_types", "jurisdictions")
    @classmethod
    def validate_unique_filter(cls, value):
        return _unique_values(value)

    @field_validator("document_date_from", "document_date_to", mode="before")
    @classmethod
    def validate_filter_date(cls, value):
        return None if value is None else _calendar_date(value)

    @field_validator("jurisdictions")
    @classmethod
    def validate_jurisdiction_filter(cls, value):
        if value is not None and any(not item.strip() for item in value):
            raise ValueError("jurisdições não podem ser vazias")
        return value

    @model_validator(mode="after")
    def validate_date_range(self):
        if (
            self.document_date_from is not None
            and self.document_date_to is not None
            and self.document_date_from > self.document_date_to
        ):
            raise ValueError("document_date_from não pode ser posterior a document_date_to")
        return self


class SearchDocumentsRequest(_DocumentFilter):
    """Consulta semântica tipada; identidade é recebida pelo contexto do runtime."""

    query: str = Field(min_length=1, max_length=4_000)
    top_k: TopK = 8


class SearchDocumentsResponse(_DocumentContract):
    hits: Annotated[tuple[DocumentSourceReference, ...], Field(max_length=20)] = ()
    corpus_revision: RevisionHash
    refresh_status: DocumentAvailability
    truncated: StrictBool = False


class GetDocumentExcerptRequest(_DocumentContract):
    document_id: UUID
    page_from: PositivePage
    page_to: PositivePage
    expected_revision: RevisionHash | None = None

    @model_validator(mode="after")
    def validate_pages(self):
        if self.page_to < self.page_from:
            raise ValueError("page_to não pode ser anterior a page_from")
        if self.page_to - self.page_from >= 3:
            raise ValueError("a consulta pode abranger no máximo três páginas")
        return self


class GetDocumentExcerptResponse(_DocumentContract):
    source: DocumentSourceReference
    corpus_revision: RevisionHash
    complete: StrictBool


class ListDocumentsRequest(_DocumentFilter):
    page_size: PageSize = 20
    cursor: BoundedCursor | None = None


class DocumentMetadata(_DocumentContract):
    document_id: UUID
    title: str = Field(min_length=1, max_length=200)
    document_type: DocumentType
    origin: str = Field(min_length=1, max_length=500)
    revision_hash: RevisionHash
    availability: DocumentAvailability
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=120)
    document_date: date | None = None
    uploaded_at: datetime | None = None
    updated_at: datetime | None = None
    was_updated: bool

    @field_validator("document_date", mode="before")
    @classmethod
    def validate_document_date(cls, value):
        return None if value is None else _calendar_date(value)

    @field_validator("uploaded_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp deve incluir timezone")
        return value


class ListDocumentsResponse(_DocumentContract):
    documents: Annotated[tuple[DocumentMetadata, ...], Field(max_length=50)] = ()
    next_cursor: BoundedCursor | None = None
    corpus_revision: RevisionHash
    truncated: StrictBool = False


class AggregateDocumentsRequest(_DocumentFilter):
    group_by: Annotated[tuple[DocumentGroupDimension, ...], Field(min_length=1, max_length=2)]

    @field_validator("group_by")
    @classmethod
    def validate_group_by(cls, value):
        return _unique_values(value)


class DocumentGroupValue(_DocumentContract):
    dimension: DocumentGroupDimension
    value: str = Field(min_length=1, max_length=120)


class DocumentAggregateGroup(_DocumentContract):
    dimensions: Annotated[tuple[DocumentGroupValue, ...], Field(min_length=1, max_length=2)]
    document_count: StrictInt = Field(ge=0)


class AggregateDocumentsResponse(_DocumentContract):
    groups: Annotated[tuple[DocumentAggregateGroup, ...], Field(max_length=100)] = ()
    count_distinct_documents: StrictInt = Field(ge=0)
    corpus_revision: RevisionHash
    complete: StrictBool
