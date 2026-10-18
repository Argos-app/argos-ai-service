"""Sincronização completa do corpus e publicação fail-closed por revisão."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid5

import pytest
from argos_a2a_agent.domain import (
    CorpusSynchronizationError,
    CorpusUpdatingError,
    DocumentChunk,
    DocumentMetadata,
    IndexedDocumentChunk,
    ManifestStoreError,
    SourceDocument,
    SourceDocumentListing,
    SourceListingIncompleteError,
    SourceReadError,
)
from argos_a2a_agent.infrastructure import DocumentCorpusSynchronizer

from packages.contracts.a2a import DocumentType, LegalValidityStatus

FINGERPRINT = "f" * 64
OTHER_FINGERPRINT = "e" * 64


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("os fakes síncronos não devem ceder controle de I/O")


def make_metadata(document_id: UUID, content: bytes, *, title: str) -> DocumentMetadata:
    return DocumentMetadata(
        document_id=document_id,
        source_key=f"fixtures/{document_id}.pdf",
        title=title,
        document_type=DocumentType.LAW,
        origin=f"fixture://global/{document_id}.pdf",
        revision_hash=sha256(content).hexdigest(),
        size_bytes=len(content),
        jurisdiction="BR",
        document_date=date(2025, 1, 1),
        legal_validity=LegalValidityStatus.UNKNOWN,
    )


def source_pair(document_id: UUID, content: bytes, *, title: str | None = None):
    metadata = make_metadata(document_id, content, title=title or f"Lei {document_id}")
    return metadata, SourceDocument(metadata=metadata, content=content)


class FakeSource:
    def __init__(self, pairs=(), *, page_size=None, incomplete_without_cursor=False):
        self.pairs = {metadata.document_id: (metadata, source) for metadata, source in pairs}
        self.page_size = page_size
        self.incomplete_without_cursor = incomplete_without_cursor
        self.list_calls = []
        self.read_calls = []
        self.mismatched_reads = set()

    async def list_documents(self, *, cursor=None):
        self.list_calls.append(cursor)
        entries = sorted(self.pairs.values(), key=lambda pair: str(pair[0].document_id))
        if self.incomplete_without_cursor:
            return SourceDocumentListing(
                documents=tuple(item[0] for item in entries), complete=False
            )
        if self.page_size is None:
            return SourceDocumentListing(
                documents=tuple(item[0] for item in entries), complete=True
            )
        page_index = int(cursor.removeprefix("page-") if cursor else "0")
        start = page_index * self.page_size
        page = entries[start : start + self.page_size]
        has_more = start + self.page_size < len(entries)
        return SourceDocumentListing(
            documents=tuple(item[0] for item in page),
            complete=not has_more,
            continuation_cursor=f"page-{page_index + 1}" if has_more else None,
        )

    async def read_pdf(self, document_id):
        self.read_calls.append(document_id)
        pair = self.pairs.get(document_id)
        if pair is None:
            return None
        metadata, source = pair
        if document_id in self.mismatched_reads:
            alternate = make_metadata(document_id, source.content, title="metadado divergente")
            return SourceDocument(metadata=alternate, content=source.content)
        return source


class FakeManifestRepository:
    def __init__(self, manifest=None):
        self.manifest = manifest
        self.saved = []
        self.fail_save = False

    async def load(self):
        return self.manifest

    async def save(self, manifest):
        if self.fail_save:
            raise ManifestStoreError("simulated manifest write failure")
        self.saved.append(manifest)
        self.manifest = manifest


class FakeProcessor:
    def __init__(self):
        self.document_ids = []

    async def execute(self, source):
        metadata = source.metadata
        self.document_ids.append(metadata.document_id)
        chunk = DocumentChunk(
            chunk_id=uuid5(metadata.document_id, metadata.revision_hash),
            document_id=metadata.document_id,
            revision_hash=metadata.revision_hash,
            chunk_index=0,
            page_from=1,
            page_to=1,
            text=f"Texto de {metadata.title}",
        )
        return (IndexedDocumentChunk(chunk, (0.1, 0.2), metadata),)


class FakeIndexWriter:
    def __init__(self):
        self.generations = {}
        self.active_revision = None
        self.copied = []
        self.upserted = []
        self.published = []
        self.discarded = []
        self.unpublished = []
        self.fail_publish_after_switch = False

    async def upsert_chunks(self, corpus_revision, chunks):
        self.upserted.append((corpus_revision, tuple(chunks)))
        generation = self.generations.setdefault(corpus_revision, {})
        for item in chunks:
            generation[item.metadata.document_id] = item.metadata

    async def remove_document(self, corpus_revision, document_id):
        self.generations.setdefault(corpus_revision, {}).pop(document_id, None)

    async def copy_documents(self, source_revision, target_revision, documents):
        self.copied.append((source_revision, target_revision, dict(documents)))
        source = self.generations.get(source_revision, {})
        if not set(documents) <= set(source):
            raise CorpusSynchronizationError("missing reusable points")
        self.generations[target_revision] = dict(documents)

    async def publish(self, corpus_revision):
        self.generations.setdefault(corpus_revision, {})
        self.active_revision = corpus_revision
        self.published.append(corpus_revision)
        if self.fail_publish_after_switch:
            self.fail_publish_after_switch = False
            raise RuntimeError("simulated timeout after alias switch")

    async def generation_exists(self, corpus_revision):
        return corpus_revision in self.generations

    async def is_published(self, corpus_revision):
        return self.active_revision == corpus_revision

    async def unpublish(self, corpus_revision):
        self.unpublished.append(corpus_revision)
        if self.active_revision == corpus_revision:
            self.active_revision = None

    async def discard(self, corpus_revision):
        if self.active_revision == corpus_revision:
            raise CorpusSynchronizationError("cannot discard active")
        if corpus_revision in self.generations:
            self.generations.pop(corpus_revision)
            self.discarded.append(corpus_revision)


def make_synchronizer(source, manifests, processor, writer, fingerprint=FINGERPRINT):
    return DocumentCorpusSynchronizer(
        source,
        manifests,
        processor,
        writer,
        processing_fingerprint=fingerprint,
    )


def seed_old_manifest(*pairs, fingerprint=FINGERPRINT):
    source = FakeSource(pairs)
    manifests = FakeManifestRepository()
    processor = FakeProcessor()
    index_writer = FakeIndexWriter()
    synchronizer = make_synchronizer(source, manifests, processor, index_writer, fingerprint)
    initial_result = run_immediate(synchronizer.ensure_current())
    assert initial_result.updated
    return source, manifests, processor, index_writer, synchronizer


def test_initial_sync_indexes_all_documents_then_publishes_manifest() -> None:
    first = source_pair(UUID(int=1), b"pdf-one")[0:2]
    second = source_pair(UUID(int=2), b"pdf-two")[0:2]
    source = FakeSource((first, second), page_size=1)
    manifests = FakeManifestRepository()
    processor = FakeProcessor()
    index_writer = FakeIndexWriter()
    synchronizer = make_synchronizer(source, manifests, processor, index_writer)

    result = run_immediate(synchronizer.ensure_current())

    assert result.updated
    assert set(result.changed_document_ids) == {first[0].document_id, second[0].document_id}
    assert result.removed_document_ids == ()
    assert source.list_calls == [None, "page-1"]
    assert set(source.read_calls) == {first[0].document_id, second[0].document_id}
    assert set(processor.document_ids) == {first[0].document_id, second[0].document_id}
    assert index_writer.active_revision == result.corpus_revision
    assert manifests.manifest.corpus_revision == result.corpus_revision


def test_unchanged_source_returns_same_revision_without_reembedding_or_rewriting() -> None:
    pair = source_pair(UUID(int=3), b"pdf-stable")
    source, manifests, processor, writer, synchronizer = seed_old_manifest(pair)
    processor.document_ids.clear()
    writer.published.clear()
    writer.upserted.clear()

    result = run_immediate(synchronizer.ensure_current())

    assert not result.updated
    assert result.corpus_revision == manifests.manifest.corpus_revision
    assert result.changed_document_ids == ()
    assert result.removed_document_ids == ()
    assert processor.document_ids == []
    assert writer.upserted == []
    assert writer.published == []
    assert source.list_calls == [None, None]


def test_update_processes_only_new_or_changed_bytes_and_copies_intact_vectors() -> None:
    id_a, id_b, id_c, id_d = (UUID(int=value) for value in (10, 11, 12, 13))
    old_pairs = (
        source_pair(id_a, b"old A"),
        source_pair(id_b, b"stable B"),
        source_pair(id_c, b"removed C"),
    )
    source, manifests, processor, writer, synchronizer = seed_old_manifest(*old_pairs)
    new_pairs = (
        source_pair(id_a, b"new A"),
        source_pair(id_b, b"stable B"),
        source_pair(id_d, b"new D"),
    )
    source.pairs = {metadata.document_id: (metadata, document) for metadata, document in new_pairs}
    processor.document_ids.clear()

    result = run_immediate(synchronizer.ensure_current())

    assert result.updated
    assert set(result.changed_document_ids) == {id_a, id_d}
    assert result.removed_document_ids == (id_c,)
    assert set(processor.document_ids) == {id_a, id_d}
    copied_ids = set(writer.copied[-1][2])
    assert copied_ids == {id_b}
    target_documents = writer.generations[result.corpus_revision]
    assert set(target_documents) == {id_a, id_b, id_d}


def test_metadata_only_change_reuses_embedding_and_refreshes_manifest_payload() -> None:
    document_id = UUID(int=20)
    original = source_pair(document_id, b"unchanged bytes", title="Lei antiga")
    source, manifests, processor, writer, synchronizer = seed_old_manifest(original)
    updated = source_pair(document_id, b"unchanged bytes", title="Lei consolidada")
    source.pairs = {document_id: updated}
    processor.document_ids.clear()

    result = run_immediate(synchronizer.ensure_current())

    assert result.changed_document_ids == (document_id,)
    assert processor.document_ids == []
    assert writer.copied[-1][2][document_id].title == "Lei consolidada"
    assert manifests.manifest.documents[0].title == "Lei consolidada"


def test_processing_fingerprint_change_rebuilds_all_current_documents() -> None:
    pairs = (source_pair(UUID(int=30), b"one"), source_pair(UUID(int=31), b"two"))
    source, manifests, processor, writer, _ = seed_old_manifest(*pairs)
    processor.document_ids.clear()
    synchronizer = make_synchronizer(
        source, manifests, processor, writer, fingerprint=OTHER_FINGERPRINT
    )

    result = run_immediate(synchronizer.ensure_current())

    assert result.updated
    assert set(result.changed_document_ids) == {item[0].document_id for item in pairs}
    assert set(processor.document_ids) == {item[0].document_id for item in pairs}
    assert writer.copied == []


def test_incomplete_listing_does_not_touch_manifest_or_index() -> None:
    source = FakeSource((source_pair(UUID(int=40), b"one"),), incomplete_without_cursor=True)
    manifests = FakeManifestRepository()
    writer = FakeIndexWriter()
    synchronizer = make_synchronizer(source, manifests, FakeProcessor(), writer)

    with pytest.raises(SourceListingIncompleteError):
        run_immediate(synchronizer.ensure_current())

    assert manifests.saved == []
    assert writer.published == []
    assert writer.discarded == []


def test_read_mismatch_discards_staging_and_keeps_previous_publication() -> None:
    original = source_pair(UUID(int=50), b"old")[0:2]
    source, manifests, processor, writer, synchronizer = seed_old_manifest(original)
    old_revision = manifests.manifest.corpus_revision
    changed = source_pair(original[0].document_id, b"new")
    source.pairs = {changed[0].document_id: changed}
    source.mismatched_reads.add(changed[0].document_id)

    with pytest.raises(SourceReadError, match="não corresponde"):
        run_immediate(synchronizer.ensure_current())

    assert manifests.manifest.corpus_revision == old_revision
    assert writer.active_revision == old_revision
    assert set(writer.generations) == {old_revision}
    assert len(writer.published) == 1


def test_manifest_failure_rolls_alias_back_and_discards_new_generation() -> None:
    original = source_pair(UUID(int=60), b"old")[0:2]
    source, manifests, processor, writer, synchronizer = seed_old_manifest(original)
    old_revision = manifests.manifest.corpus_revision
    changed = source_pair(original[0].document_id, b"new")
    source.pairs = {changed[0].document_id: changed}
    manifests.fail_save = True

    with pytest.raises(ManifestStoreError):
        run_immediate(synchronizer.ensure_current())

    assert writer.active_revision == old_revision
    assert manifests.manifest.corpus_revision == old_revision
    assert writer.discarded[-1] != old_revision


def test_ambiguous_publish_timeout_is_rolled_back_to_last_confirmed_generation() -> None:
    original = source_pair(UUID(int=70), b"old")[0:2]
    source, manifests, processor, writer, synchronizer = seed_old_manifest(original)
    old_revision = manifests.manifest.corpus_revision
    changed = source_pair(original[0].document_id, b"new")
    source.pairs = {changed[0].document_id: changed}
    writer.fail_publish_after_switch = True

    with pytest.raises(CorpusSynchronizationError, match="publicar a geração"):
        run_immediate(synchronizer.ensure_current())

    assert writer.active_revision == old_revision
    assert manifests.manifest.corpus_revision == old_revision
    assert writer.discarded[-1] != old_revision


def test_processing_failure_discards_staging_without_publishing() -> None:
    original = source_pair(UUID(int=80), b"old")[0:2]
    source, manifests, _, writer, synchronizer = seed_old_manifest(original)
    old_revision = manifests.manifest.corpus_revision
    changed = source_pair(original[0].document_id, b"new")
    source.pairs = {changed[0].document_id: changed}

    class FailedProcessor:
        async def execute(self, source_document):
            raise RuntimeError("simulated embedding failure")

    failing_sync = make_synchronizer(source, manifests, FailedProcessor(), writer)
    with pytest.raises(CorpusSynchronizationError, match="preparar"):
        run_immediate(failing_sync.ensure_current())

    assert writer.active_revision == old_revision
    assert manifests.manifest.corpus_revision == old_revision
    assert set(writer.generations) == {old_revision}


def test_locked_synchronizer_returns_updating_before_reading_source() -> None:
    source = FakeSource((source_pair(UUID(int=90), b"one"),))
    synchronizer = make_synchronizer(
        source, FakeManifestRepository(), FakeProcessor(), FakeIndexWriter()
    )
    assert run_immediate(synchronizer._lock.acquire())

    with pytest.raises(CorpusUpdatingError):
        run_immediate(synchronizer.ensure_current())

    assert source.list_calls == []
    synchronizer._lock.release()


def test_manifest_revision_is_stable_for_input_order_and_timezone_equivalence() -> None:
    first_id = UUID(int=101)
    second_id = UUID(int=102)
    first = source_pair(first_id, b"one")[0]
    second = source_pair(second_id, b"two")[0]
    synchronizer = make_synchronizer(
        FakeSource(), FakeManifestRepository(), FakeProcessor(), FakeIndexWriter()
    )

    assert synchronizer._revision((first, second)) == synchronizer._revision((second, first))
    utc_time = datetime(2025, 1, 1, tzinfo=UTC)
    local_time = utc_time.astimezone(timezone(timedelta(hours=-3)))
    assert synchronizer._revision((replace(first, source_modified_at=utc_time),)) == (
        synchronizer._revision((replace(first, source_modified_at=local_time),))
    )
