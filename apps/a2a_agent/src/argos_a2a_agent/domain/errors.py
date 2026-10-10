"""Erros seguros para falhas esperadas do corpus documental."""


class DocumentDomainError(Exception):
    """Erro de domínio traduzível pelo transporte sem expor detalhes internos."""

    code = "document_domain_error"


class DocumentNotFoundError(DocumentDomainError):
    code = "document_not_found"


class SourceReadError(DocumentDomainError):
    code = "document_source_read_failed"


class SourceListingIncompleteError(DocumentDomainError):
    code = "document_source_listing_incomplete"


class ManifestStoreError(DocumentDomainError):
    code = "document_manifest_unavailable"


class PdfExtractionError(DocumentDomainError):
    code = "document_pdf_extraction_failed"


class EmbeddingProviderError(DocumentDomainError):
    code = "document_embedding_failed"


class IndexReadError(DocumentDomainError):
    code = "document_index_read_failed"


class IndexWriteError(DocumentDomainError):
    code = "document_index_write_failed"


class StaleDocumentRevisionError(DocumentDomainError):
    code = "document_revision_stale"


class CorpusSynchronizationError(DocumentDomainError):
    code = "document_corpus_synchronization_failed"
