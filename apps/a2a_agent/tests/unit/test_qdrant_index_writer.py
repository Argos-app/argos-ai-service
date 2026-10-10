"""Escrita Qdrant limitada a gerações de staging e publicação por alias."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from argos_a2a_agent.domain import (
    DocumentChunk,
    DocumentMetadata,
    IndexedDocumentChunk,
    IndexWriteError,
)
from argos_a2a_agent.infrastructure import QdrantDocumentIndexWriter
from qdrant_client.models import (
    CreateAliasOperation,
    DeleteAliasOperation,
    Distance,
)

from packages.contracts.a2a import DocumentType, LegalValidityStatus

REVISION = "a" * 64


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("o fake Qdrant não deve ceder controle de I/O")


def indexed_chunk(*, revision: str = REVISION, chunk_id=None) -> IndexedDocumentChunk:
    document_id = uuid4()
    metadata = DocumentMetadata(
        document_id=document_id,
        source_key="fixtures/lei.pdf",
        title="Lei sintética",
        document_type=DocumentType.LAW,
        origin="fixture://global/lei.pdf",
        revision_hash=revision,
        size_bytes=100,
        jurisdiction="BR",
        document_date=date(2025, 1, 1),
        legal_validity=LegalValidityStatus.UNKNOWN,
    )
    chunk = DocumentChunk(
        chunk_id=chunk_id or uuid4(),
        document_id=document_id,
        revision_hash=revision,
        chunk_index=0,
        page_from=2,
        page_to=2,
        text="Trecho legal sintético.",
    )
    return IndexedDocumentChunk(chunk=chunk, vector=(0.1, 0.2, 0.3), metadata=metadata)


class FakeQdrantClient:
    def __init__(self):
        self.collections = {}
        self.aliases = {}
        self.upsert_calls = []
        self.delete_calls = []
        self.alias_update_calls = []
        self.deleted_collections = []
        self.fail_upsert = False

    async def collection_exists(self, *, collection_name):
        return collection_name in self.collections

    async def create_collection(self, *, collection_name, vectors_config):
        self.collections[collection_name] = vectors_config
        return True

    async def get_collection(self, *, collection_name):
        vectors = self.collections[collection_name]
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

    async def upsert(self, *, collection_name, points, wait):
        if self.fail_upsert:
            raise RuntimeError("simulated Qdrant failure")
        self.upsert_calls.append((collection_name, points, wait))

    async def delete(self, *, collection_name, points_selector, wait):
        self.delete_calls.append((collection_name, points_selector, wait))

    async def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(alias_name=alias, collection_name=collection)
                for alias, collection in self.aliases.items()
            ]
        )

    async def update_collection_aliases(self, *, change_aliases_operations):
        self.alias_update_calls.append(change_aliases_operations)
        for operation in change_aliases_operations:
            if isinstance(operation, DeleteAliasOperation):
                self.aliases.pop(operation.delete_alias.alias_name, None)
            if isinstance(operation, CreateAliasOperation):
                alias = operation.create_alias
                self.aliases[alias.alias_name] = alias.collection_name

    async def delete_collection(self, *, collection_name):
        self.deleted_collections.append(collection_name)
        self.collections.pop(collection_name, None)


def writer(client=None, **settings):
    settings.setdefault("distance", "Cosine")
    return QdrantDocumentIndexWriter(
        client or FakeQdrantClient(),
        active_alias="documents",
        vector_size=3,
        **settings,
    )


def test_upsert_writes_only_allowlisted_payload_into_revision_staging() -> None:
    client = FakeQdrantClient()
    adapter = writer(client)
    item = indexed_chunk()

    run_immediate(adapter.upsert_chunks(REVISION, (item,)))

    collection, points, wait = client.upsert_calls[0]
    assert collection == f"documents__g_{REVISION}"
    assert wait is True
    point = points[0]
    assert point.id == str(item.chunk.chunk_id)
    assert point.vector == list(item.vector)
    assert point.payload == {
        "corpus_revision": REVISION,
        "document_id": str(item.chunk.document_id),
        "source_key": "fixtures/lei.pdf",
        "revision_hash": REVISION,
        "document_type": "law",
        "title": "Lei sintética",
        "origin": "fixture://global/lei.pdf",
        "jurisdiction": "BR",
        "source_etag": None,
        "source_modified_at": None,
        "document_date": "2025-01-01",
        "size_bytes": 100,
        "uploaded_at": None,
        "updated_at": None,
        "legal_validity": "unknown",
        "chunk_index": 0,
        "page_from": 2,
        "page_to": 2,
        "text": "Trecho legal sintético.",
    }
    assert client.collections[collection].size == 3
    assert client.collections[collection].distance is Distance.COSINE


def test_upsert_rejects_invalid_revision_duplicate_ids_and_wrong_vector_size() -> None:
    client = FakeQdrantClient()
    adapter = writer(client)
    item = indexed_chunk()

    with pytest.raises(IndexWriteError, match="SHA-256"):
        run_immediate(adapter.upsert_chunks("invalid", (item,)))
    with pytest.raises(IndexWriteError, match="duplicados"):
        run_immediate(adapter.upsert_chunks(REVISION, (item, item)))
    wrong_dimension = IndexedDocumentChunk(
        chunk=item.chunk,
        vector=(0.1, 0.2),
        metadata=item.metadata,
    )
    with pytest.raises(IndexWriteError, match="dimensão"):
        run_immediate(adapter.upsert_chunks(REVISION, (wrong_dimension,)))
    assert client.upsert_calls == []


def test_upsert_and_remove_are_scoped_to_exact_staging_generation() -> None:
    client = FakeQdrantClient()
    adapter = writer(client)
    item = indexed_chunk()

    with pytest.raises(IndexWriteError, match="geração"):
        run_immediate(adapter.upsert_chunks("b" * 64, (item,)))
    run_immediate(adapter.remove_document(REVISION, item.chunk.document_id))

    collection, selector, wait = client.delete_calls[0]
    assert collection == f"documents__g_{REVISION}"
    assert selector.filter.must[0].key == "document_id"
    assert selector.filter.must[0].match.value == str(item.chunk.document_id)
    assert wait is True


def test_publish_atomically_replaces_active_alias_and_preserves_old_generation() -> None:
    client = FakeQdrantClient()
    old_generation = "documents__g_" + "b" * 64
    client.collections[old_generation] = SimpleNamespace(size=3, distance=Distance.COSINE)
    client.aliases["documents"] = old_generation
    adapter = writer(client)

    run_immediate(adapter.publish(REVISION))

    new_generation = f"documents__g_{REVISION}"
    assert client.aliases["documents"] == new_generation
    assert old_generation in client.collections
    assert new_generation in client.collections
    assert len(client.alias_update_calls) == 1
    assert len(client.alias_update_calls[0]) == 2


def test_publish_is_idempotent_when_alias_already_points_to_generation() -> None:
    client = FakeQdrantClient()
    generation = f"documents__g_{REVISION}"
    client.collections[generation] = SimpleNamespace(size=3, distance=Distance.COSINE)
    client.aliases["documents"] = generation

    run_immediate(writer(client).publish(REVISION))

    assert client.alias_update_calls == []


def test_discard_removes_only_unpublished_generation_and_protects_active_one() -> None:
    client = FakeQdrantClient()
    generation = f"documents__g_{REVISION}"
    client.collections[generation] = SimpleNamespace(size=3, distance=Distance.COSINE)
    adapter = writer(client)

    run_immediate(adapter.discard(REVISION))
    assert client.deleted_collections == [generation]

    client.collections[generation] = SimpleNamespace(size=3, distance=Distance.COSINE)
    client.aliases["documents"] = generation
    with pytest.raises(IndexWriteError, match="descartar"):
        run_immediate(adapter.discard(REVISION))
    assert client.collections.get(generation) is not None


def test_qdrant_failures_are_mapped_to_safe_index_write_error() -> None:
    client = FakeQdrantClient()
    client.fail_upsert = True

    with pytest.raises(IndexWriteError, match="staging Qdrant"):
        run_immediate(writer(client).upsert_chunks(REVISION, (indexed_chunk(),)))


def test_existing_generation_with_incompatible_vector_configuration_is_rejected() -> None:
    client = FakeQdrantClient()
    generation = f"documents__g_{REVISION}"
    client.collections[generation] = SimpleNamespace(size=2, distance=Distance.COSINE)

    with pytest.raises(IndexWriteError, match="configuração vetorial"):
        run_immediate(writer(client).upsert_chunks(REVISION, (indexed_chunk(),)))


def test_writer_rejects_unsafe_alias_and_invalid_vector_configuration() -> None:
    client = FakeQdrantClient()
    with pytest.raises(ValueError, match="active_alias"):
        QdrantDocumentIndexWriter(
            client, active_alias="../collection", vector_size=3, distance="Cosine"
        )
    with pytest.raises(ValueError, match="vector_size"):
        QdrantDocumentIndexWriter(
            client, active_alias="documents", vector_size=0, distance="Cosine"
        )
    with pytest.raises(ValueError, match="distance"):
        QdrantDocumentIndexWriter(
            client, active_alias="documents", vector_size=3, distance="unknown"
        )
