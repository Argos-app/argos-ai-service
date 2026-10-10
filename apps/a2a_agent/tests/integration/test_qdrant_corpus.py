"""Homologação do corpus sincronizado usando Qdrant real descartável."""

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from argos_a2a_agent.adapters.documents import PyPdfParser
from argos_a2a_agent.adapters.storage import LocalDocumentSource, LocalManifestRepository
from argos_a2a_agent.application import PrepareDocumentForIndex, TextChunker
from argos_a2a_agent.infrastructure import (
    DocumentCorpusSynchronizer,
    QdrantDocumentIndexWriter,
    ReadOnlyQdrantReader,
)
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("A2A_RUN_INTEGRATION") != "1",
        reason="suba o compose e defina A2A_RUN_INTEGRATION=1",
    ),
]

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "apps/a2a_agent/tests/fixtures/documents"
QDRANT_URL = os.getenv("A2A_TEST_QDRANT_URL", "http://127.0.0.1:6338")
WRITE_KEY = os.getenv("A2A_TEST_QDRANT_WRITE_KEY", "a2a-integration-write-key")
READ_KEY = os.getenv("A2A_TEST_QDRANT_READ_KEY", "a2a-integration-read-key")
VECTOR_SIZE = 3


class DeterministicEmbeddings:
    """Stub reproduzível que evita chamada a um provedor externo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts):
        batch = tuple(texts)
        self.calls.append(batch)
        return tuple((float(len(text)), 1.0, 0.5) for text in batch)


@dataclass
class CorpusHarness:
    read_client: AsyncQdrantClient
    write_client: AsyncQdrantClient
    alias: str
    source_root: Path
    manifest_path: Path
    source: LocalDocumentSource
    synchronizer: DocumentCorpusSynchronizer
    embeddings: DeterministicEmbeddings
    writer: QdrantDocumentIndexWriter
    reader: ReadOnlyQdrantReader
    documents: list[dict]


async def _wait_for_qdrant(client: AsyncQdrantClient) -> None:
    for _ in range(60):
        try:
            await client.get_collections()
            return
        except Exception:
            await asyncio.sleep(0.25)
    pytest.fail("Qdrant de integração não respondeu; suba o compose antes dos testes")


@pytest_asyncio.fixture
async def corpus_harness(tmp_path: Path):
    read_client = AsyncQdrantClient(url=QDRANT_URL, api_key=READ_KEY, timeout=3)
    write_client = AsyncQdrantClient(url=QDRANT_URL, api_key=WRITE_KEY, timeout=3)
    alias = f"a2a_it_{os.urandom(8).hex()}"
    harness: CorpusHarness | None = None
    try:
        await _wait_for_qdrant(read_client)
        source_root = tmp_path / "source"
        shutil.copytree(FIXTURES, source_root)
        catalog = json.loads((source_root / "catalog.json").read_text(encoding="utf-8"))
        source = LocalDocumentSource(source_root)
        manifest_path = tmp_path / "manifest.json"
        embeddings = DeterministicEmbeddings()
        processor = PrepareDocumentForIndex(
            PyPdfParser(), embeddings, TextChunker(), embedding_batch_size=4
        )
        writer = QdrantDocumentIndexWriter(
            write_client,
            active_alias=alias,
            vector_size=VECTOR_SIZE,
            distance=Distance.COSINE.value,
        )
        harness = CorpusHarness(
            read_client=read_client,
            write_client=write_client,
            alias=alias,
            source_root=source_root,
            manifest_path=manifest_path,
            source=source,
            synchronizer=DocumentCorpusSynchronizer(
                source,
                LocalManifestRepository(manifest_path),
                processor,
                writer,
                processing_fingerprint="a" * 64,
            ),
            embeddings=embeddings,
            writer=writer,
            reader=ReadOnlyQdrantReader(read_client, alias),
            documents=catalog["documents"],
        )
        yield harness
    finally:
        if harness is not None:
            from qdrant_client.models import DeleteAlias, DeleteAliasOperation

            aliases = await write_client.get_aliases()
            if any(item.alias_name == alias for item in aliases.aliases):
                await write_client.update_collection_aliases(
                    change_aliases_operations=[
                        DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias))
                    ]
                )
            collections = await write_client.get_collections()
            for item in collections.collections:
                if item.name.startswith(f"{alias}__g_"):
                    await write_client.delete_collection(collection_name=item.name)
        await read_client.close()
        await write_client.close()


def _revision_filter(revision: str, document_type: str | None = None) -> dict:
    conditions = [
        {"key": "corpus_revision", "match": {"value": revision}},
    ]
    if document_type is not None:
        conditions.append({"key": "document_type", "match": {"value": document_type}})
    return {"must": conditions}


@pytest.mark.asyncio
async def test_real_qdrant_sync_search_and_manifest_restart(corpus_harness: CorpusHarness) -> None:
    harness = corpus_harness
    before = {
        item.name: item.read_bytes() for item in harness.source_root.iterdir() if item.is_file()
    }

    first = await harness.synchronizer.ensure_current()

    assert first.updated
    assert len(harness.embeddings.calls) > 0
    law_id = UUID("11111111-1111-4111-8111-111111111111")
    law_filter = _revision_filter(first.corpus_revision, "law")
    terms_filter = _revision_filter(first.corpus_revision, "terms_conditions")
    assert await harness.reader.count(query_filter=law_filter) > 0
    assert await harness.reader.count(query_filter=terms_filter) > 0
    matches = await harness.reader.query_points((1.0, 0.2, 0.1), query_filter=law_filter, limit=3)
    assert matches
    assert all(item["payload"]["document_id"] == str(law_id) for item in matches)
    assert all(
        "property_id" not in item["payload"] and "property_ids" not in item["payload"]
        for item in matches
    )
    point_id = matches[0]["id"]
    assert (await harness.reader.retrieve([point_id]))[0]["payload"]["corpus_revision"] == (
        first.corpus_revision
    )

    embedding_call_count = len(harness.embeddings.calls)
    second = await harness.synchronizer.ensure_current()
    assert not second.updated
    assert len(harness.embeddings.calls) == embedding_call_count

    restarted_manifest = LocalManifestRepository(harness.manifest_path)
    restarted_sync = DocumentCorpusSynchronizer(
        harness.source,
        restarted_manifest,
        PrepareDocumentForIndex(PyPdfParser(), harness.embeddings),
        harness.writer,
        processing_fingerprint="a" * 64,
    )
    third = await restarted_sync.ensure_current()
    assert not third.updated
    assert third.corpus_revision == first.corpus_revision
    after = {
        item.name: item.read_bytes() for item in harness.source_root.iterdir() if item.is_file()
    }
    assert after == before


@pytest.mark.asyncio
async def test_metadata_revision_reuses_vectors_and_alias_hides_previous_revision(
    corpus_harness: CorpusHarness,
) -> None:
    harness = corpus_harness
    first = await harness.synchronizer.ensure_current()
    previous_filter = _revision_filter(first.corpus_revision)
    previous_count = await harness.reader.count(query_filter=previous_filter)
    embedding_call_count = len(harness.embeddings.calls)

    harness.documents[0]["title"] = "Título atualizado apenas para homologação"
    (harness.source_root / "catalog.json").write_text(
        json.dumps({"documents": harness.documents}), encoding="utf-8"
    )
    updated = await harness.synchronizer.ensure_current()

    assert updated.updated
    assert updated.corpus_revision != first.corpus_revision
    assert len(harness.embeddings.calls) == embedding_call_count
    assert await harness.reader.count(query_filter=previous_filter) == 0
    current = await harness.reader.query_points(
        (1.0, 0.2, 0.1), query_filter=_revision_filter(updated.corpus_revision), limit=20
    )
    assert len(current) == previous_count
    assert any(
        point["payload"]["title"] == "Título atualizado apenas para homologação"
        for point in current
    )


@pytest.mark.asyncio
async def test_read_only_qdrant_credential_rejects_all_mutations(
    corpus_harness: CorpusHarness,
) -> None:
    harness = corpus_harness
    result = await harness.synchronizer.ensure_current()
    points = await harness.reader.query_points(
        (1.0, 0.2, 0.1),
        query_filter=_revision_filter(result.corpus_revision),
        limit=1,
    )
    assert points
    point_id = points[0]["id"]

    from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

    operations = (
        lambda: harness.read_client.upsert(
            collection_name=harness.alias,
            points=[PointStruct(id=point_id, vector=[0.1, 0.2, 0.3], payload={})],
            wait=True,
        ),
        lambda: harness.read_client.delete(
            collection_name=harness.alias,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=points[0]["payload"]["document_id"]),
                        )
                    ]
                )
            ),
            wait=True,
        ),
        lambda: harness.read_client.create_collection(
            collection_name=f"{harness.alias}_forbidden",
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        ),
    )
    for operation in operations:
        with pytest.raises(UnexpectedResponse) as error:
            await operation()
        assert error.value.status_code in {401, 403}


@pytest.mark.asyncio
async def test_qdrant_reader_rejects_unbounded_filters_and_excessive_limits(
    corpus_harness: CorpusHarness,
) -> None:
    harness = corpus_harness
    with pytest.raises(ValueError, match="filtro não vazio"):
        await harness.reader.query_points((1.0, 0.0, 0.0), query_filter={}, limit=1)
    with pytest.raises(ValueError, match="entre 1 e 20"):
        await harness.reader.query_points(
            (1.0, 0.0, 0.0),
            query_filter={"must": [{"key": "document_type", "match": {"value": "law"}}]},
            limit=21,
        )
