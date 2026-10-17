"""Casos de uso protegidos para consultar o corpus documental global."""

import math
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from argos_a2a_agent.application.ensure_current_corpus import (
    EnsureCurrentCorpus,
    EnsureCurrentCorpusOutcome,
)
from argos_a2a_agent.domain.errors import (
    CorpusSynchronizationError,
    CorpusUpdatingError,
    DocumentNotFoundError,
    DocumentQueryTooBroadError,
    EmbeddingProviderError,
    IndexReadError,
    StaleDocumentRevisionError,
)
from argos_a2a_agent.domain.models import DocumentFilter
from argos_a2a_agent.domain.models import DocumentMetadata as CorpusDocumentMetadata
from argos_a2a_agent.ports.document_read_repository import DocumentReadRepository
from argos_a2a_agent.ports.embedding_provider import EmbeddingProvider
from packages.contracts.a2a import (
    CorpusRefreshStatus,
    DocumentSourceReference,
)
from packages.contracts.documents import (
    AggregateDocumentsRequest,
    AggregateDocumentsResponse,
    DocumentAggregateGroup,
    DocumentAvailability,
    DocumentGroupDimension,
    DocumentGroupValue,
    GetDocumentExcerptRequest,
    GetDocumentExcerptResponse,
    ListDocumentsRequest,
    ListDocumentsResponse,
    SearchDocumentsRequest,
    SearchDocumentsResponse,
)
from packages.contracts.documents import (
    DocumentMetadata as DocumentMetadataResponse,
)
from packages.security.access import DocumentAccessContext

_MAX_EXCERPT_CHARS = 2_000
_MAX_SEARCH_HITS = 20
_MAX_AGGREGATE_DOCUMENTS = 10_000
_MAX_AGGREGATE_GROUPS = 100
_LIST_PAGE_SIZE = 50


class _DocumentQuery:
    def __init__(
        self,
        ensure_current: EnsureCurrentCorpus,
        repository: DocumentReadRepository,
    ) -> None:
        self._ensure_current = ensure_current
        self._repository = repository

    async def _ensure_ready(
        self, context: DocumentAccessContext | None
    ) -> EnsureCurrentCorpusOutcome:
        outcome = await self._ensure_current.execute(context)
        if outcome.status == CorpusRefreshStatus.UPDATING:
            raise CorpusUpdatingError("o corpus documental está sendo atualizado")
        if outcome.status == CorpusRefreshStatus.UNAVAILABLE:
            raise CorpusSynchronizationError("não foi possível confirmar o corpus atual")
        if outcome.corpus_revision is None:
            raise CorpusSynchronizationError("a sincronização não confirmou uma revisão")
        return outcome

    async def _verify_before_return(
        self,
        context: DocumentAccessContext | None,
        corpus_revision: str,
    ) -> None:
        # O repositório compara a revisão exigida ao manifesto atual. Assim uma
        # publicação concorrente invalida a resposta em vez de liberar conteúdo antigo.
        await self._repository.count_documents(
            filters=DocumentFilter(corpus_revision=corpus_revision)
        )
        await self._ensure_current.reauthorize(context)

    async def _read_all_documents(
        self,
        filters: DocumentFilter,
        *,
        expected_count: int,
    ) -> tuple[CorpusDocumentMetadata, ...]:
        documents: list[CorpusDocumentMetadata] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await self._repository.list_documents(
                filters=filters,
                page_size=_LIST_PAGE_SIZE,
                cursor=cursor,
            )
            documents.extend(page.documents)
            if page.complete:
                break
            if page.next_cursor is None or page.next_cursor in seen_cursors:
                raise IndexReadError("a paginação do manifesto não avançou")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
            if len(documents) > _MAX_AGGREGATE_DOCUMENTS:
                raise DocumentQueryTooBroadError(
                    "a consulta excede o limite de 10000 documentos"
                )
        if len(documents) != expected_count:
            raise IndexReadError("a listagem não corresponde à contagem distinta do manifesto")
        return tuple(documents)


class SearchDocuments(_DocumentQuery):
    """Gera embedding da pergunta e devolve fontes atuais com páginas e revisão."""

    def __init__(
        self,
        ensure_current: EnsureCurrentCorpus,
        repository: DocumentReadRepository,
        embeddings: EmbeddingProvider,
    ) -> None:
        super().__init__(ensure_current, repository)
        self._embeddings = embeddings

    async def execute(
        self,
        request: SearchDocumentsRequest,
        context: DocumentAccessContext | None,
    ) -> SearchDocumentsResponse:
        current = await self._ensure_ready(context)
        revision = current.corpus_revision
        assert revision is not None
        filters = _filters(request, revision)

        matching_documents = await self._repository.count_documents(filters=filters)
        if matching_documents:
            filters = await self._scope_to_explicit_title(
                request,
                filters,
                matching_documents=matching_documents,
            )
            matching_documents = await self._repository.count_documents(filters=filters)
        if matching_documents:
            vector = await self._embed_query(request.query)
            # Busca um elemento adicional para indicar truncamento quando o
            # limite pedido é inferior ao teto do contrato.
            query_limit = min(request.top_k + 1, _MAX_SEARCH_HITS)
            result_hits = await self._repository.search(
                vector,
                filters=filters,
                limit=query_limit,
            )
            truncated = (
                len(result_hits) > request.top_k
                if request.top_k < _MAX_SEARCH_HITS
                else len(result_hits) == _MAX_SEARCH_HITS
            )
            hits = result_hits[: request.top_k]
        else:
            hits = ()
            truncated = False

        metadata_by_id = await self._metadata_for_ids(
            tuple(dict.fromkeys(hit.chunk.document_id for hit in hits)),
            filters,
        )
        sources = tuple(
            _source(
                metadata_by_id[hit.chunk.document_id],
                hit.chunk.page_from,
                hit.chunk.page_to,
                hit.chunk.text,
                hit.chunk.document_id in current.changed_document_ids,
            )
            for hit in hits
        )
        await self._verify_before_return(context, revision)
        return SearchDocumentsResponse(
            hits=sources,
            corpus_revision=revision,
            refresh_status=DocumentAvailability.READY,
            truncated=truncated,
        )

    async def _scope_to_explicit_title(
        self,
        request: SearchDocumentsRequest,
        filters: DocumentFilter,
        *,
        matching_documents: int,
    ) -> DocumentFilter:
        """Restringe busca quando a pergunta cita sem ambiguidade um título do corpus."""
        if request.document_ids is not None or matching_documents > _MAX_AGGREGATE_DOCUMENTS:
            return filters
        query = _normalize_title(request.query)
        documents = await self._read_all_documents(
            filters,
            expected_count=matching_documents,
        )
        explicit_matches = tuple(
            document
            for document in documents
            if _normalize_title(document.title) in query
        )
        if len(explicit_matches) != 1:
            return filters
        return replace(filters, document_ids=(explicit_matches[0].document_id,))

    async def _embed_query(self, query: str) -> tuple[float, ...]:
        try:
            vectors = await self._embeddings.embed((query,))
        except Exception as exc:
            if isinstance(exc, EmbeddingProviderError):
                raise
            raise EmbeddingProviderError("não foi possível vetorizar a consulta") from exc
        if (
            not isinstance(vectors, Sequence)
            or isinstance(vectors, (str, bytes))
            or len(vectors) != 1
        ):
            raise EmbeddingProviderError("provedor não retornou um vetor para a consulta")
        vector = vectors[0]
        if (
            not isinstance(vector, Sequence)
            or isinstance(vector, (str, bytes))
            or not vector
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            )
        ):
            raise EmbeddingProviderError("provedor retornou vetor inválido")
        return tuple(float(value) for value in vector)

    async def _metadata_for_ids(
        self,
        document_ids: tuple[UUID, ...],
        filters: DocumentFilter,
    ) -> dict[UUID, CorpusDocumentMetadata]:
        if not document_ids:
            return {}
        page = await self._repository.list_documents(
            filters=DocumentFilter(
                document_ids=document_ids,
                document_types=filters.document_types,
                jurisdictions=filters.jurisdictions,
                document_date_from=filters.document_date_from,
                document_date_to=filters.document_date_to,
                corpus_revision=filters.corpus_revision,
            ),
            page_size=len(document_ids),
            cursor=None,
        )
        metadata = {item.document_id: item for item in page.documents}
        if len(metadata) != len(document_ids):
            raise IndexReadError("metadados atuais não correspondem aos trechos recuperados")
        return metadata


class GetDocumentExcerpt(_DocumentQuery):
    """Recupera até três páginas do documento e sinaliza corte do trecho."""

    async def execute(
        self,
        request: GetDocumentExcerptRequest,
        context: DocumentAccessContext | None,
    ) -> GetDocumentExcerptResponse:
        current = await self._ensure_ready(context)
        revision = current.corpus_revision
        assert revision is not None
        page = await self._repository.list_documents(
            filters=DocumentFilter(
                document_ids=(request.document_id,),
                corpus_revision=revision,
            ),
            page_size=1,
            cursor=None,
        )
        if not page.documents:
            raise DocumentNotFoundError("documento não está no corpus publicado")
        metadata = page.documents[0]
        if (
            request.expected_revision is not None
            and request.expected_revision != metadata.revision_hash
        ):
            raise StaleDocumentRevisionError("a revisão do documento foi substituída")

        chunks = await self._repository.get_chunks(
            request.document_id,
            revision_hash=metadata.revision_hash,
            page_from=request.page_from,
            page_to=request.page_to,
        )
        if not chunks:
            raise IndexReadError("não há trecho indexado nas páginas solicitadas")
        full_text = "\n".join(chunk.text for chunk in chunks).strip()
        excerpt = full_text[:_MAX_EXCERPT_CHARS]
        actual_page_from = min(chunk.page_from for chunk in chunks)
        actual_page_to = max(chunk.page_to for chunk in chunks)
        source = _source(
            metadata,
            actual_page_from,
            actual_page_to,
            excerpt,
            request.document_id in current.changed_document_ids,
        )
        await self._verify_before_return(context, revision)
        return GetDocumentExcerptResponse(
            source=source,
            corpus_revision=revision,
            complete=len(full_text) <= _MAX_EXCERPT_CHARS,
        )


class ListDocuments(_DocumentQuery):
    """Lista metadados publicados com cursor e limite do contrato."""

    async def execute(
        self,
        request: ListDocumentsRequest,
        context: DocumentAccessContext | None,
    ) -> ListDocumentsResponse:
        current = await self._ensure_ready(context)
        revision = current.corpus_revision
        assert revision is not None
        result = await self._repository.list_documents(
            filters=_filters(request, revision),
            page_size=request.page_size,
            cursor=request.cursor,
        )
        response_documents = tuple(
            DocumentMetadataResponse(
                document_id=item.document_id,
                title=item.title,
                document_type=item.document_type,
                origin=item.origin,
                revision_hash=item.revision_hash,
                availability=DocumentAvailability.READY,
                jurisdiction=item.jurisdiction,
                document_date=item.document_date,
                uploaded_at=item.uploaded_at,
                updated_at=item.updated_at,
                was_updated=item.document_id in current.changed_document_ids,
            )
            for item in result.documents
        )
        await self._verify_before_return(context, revision)
        return ListDocumentsResponse(
            documents=response_documents,
            next_cursor=result.next_cursor,
            corpus_revision=revision,
            truncated=result.next_cursor is not None,
        )


class AggregateDocuments(_DocumentQuery):
    """Agrega documentos distintos sobre o manifesto completo e filtros tipados."""

    async def execute(
        self,
        request: AggregateDocumentsRequest,
        context: DocumentAccessContext | None,
    ) -> AggregateDocumentsResponse:
        current = await self._ensure_ready(context)
        revision = current.corpus_revision
        assert revision is not None
        filters = _filters(request, revision)
        document_count = await self._repository.count_documents(filters=filters)
        if document_count > _MAX_AGGREGATE_DOCUMENTS:
            raise DocumentQueryTooBroadError(
                "o filtro corresponde a mais de 10000 documentos; restrinja a consulta"
            )

        documents = await self._read_all_documents(filters, expected_count=document_count)
        counters: Counter[tuple[str, ...]] = Counter()
        for item in documents:
            key = tuple(_group_value(item, dimension) for dimension in request.group_by)
            counters[key] += 1
        if len(counters) > _MAX_AGGREGATE_GROUPS:
            raise DocumentQueryTooBroadError(
                "a agregação excede 100 grupos; restrinja os filtros ou dimensões"
            )
        groups = tuple(
            DocumentAggregateGroup(
                dimensions=tuple(
                    DocumentGroupValue(dimension=dimension, value=value)
                    for dimension, value in zip(request.group_by, key, strict=True)
                ),
                document_count=count,
            )
            for key, count in sorted(counters.items())
        )
        await self._verify_before_return(context, revision)
        return AggregateDocumentsResponse(
            groups=groups,
            count_distinct_documents=document_count,
            corpus_revision=revision,
            complete=True,
        )

def _filters(
    request: SearchDocumentsRequest | ListDocumentsRequest | AggregateDocumentsRequest,
    revision: str,
) -> DocumentFilter:
    return DocumentFilter(
        document_ids=request.document_ids,
        document_types=(
            tuple(request.document_types) if request.document_types is not None else None
        ),
        jurisdictions=(tuple(request.jurisdictions) if request.jurisdictions is not None else None),
        document_date_from=request.document_date_from,
        document_date_to=request.document_date_to,
        corpus_revision=revision,
    )


def _normalize_title(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if character.isalnum())


def _group_value(
    metadata: CorpusDocumentMetadata,
    dimension: DocumentGroupDimension,
) -> str:
    if dimension == DocumentGroupDimension.DOCUMENT_TYPE:
        return metadata.document_type.value
    if dimension == DocumentGroupDimension.JURISDICTION:
        return metadata.jurisdiction or "unknown"
    if dimension == DocumentGroupDimension.DOCUMENT_YEAR:
        return str(metadata.document_date.year) if metadata.document_date else "unknown"
    if dimension == DocumentGroupDimension.AVAILABILITY:
        return DocumentAvailability.READY.value
    raise ValueError("dimensão de agregação não suportada")


def _source(
    metadata: CorpusDocumentMetadata,
    page_from: int,
    page_to: int,
    text: str,
    was_updated: bool,
) -> DocumentSourceReference:
    return DocumentSourceReference(
        document_id=metadata.document_id,
        title=metadata.title,
        document_type=metadata.document_type,
        origin=metadata.origin,
        revision_hash=metadata.revision_hash,
        page_from=page_from,
        page_to=page_to,
        excerpt=text[:_MAX_EXCERPT_CHARS],
        jurisdiction=metadata.jurisdiction,
        document_date=metadata.document_date,
        uploaded_at=metadata.uploaded_at,
        updated_at=metadata.updated_at,
        was_updated=was_updated,
        legal_validity=metadata.legal_validity,
    )
