from types import SimpleNamespace

import pytest
from argos_a2a_agent.infrastructure.qdrant_health import check_qdrant_collection


class _Client:
    def __init__(self, vectors=None, *, exists=True, error=None):
        self.vectors = vectors
        self.exists = exists
        self.error = error
        self.collection_name = None
        self.exists_collection_name = None

    async def collection_exists(self, *, collection_name):
        self.exists_collection_name = collection_name
        if self.error is not None:
            raise self.error
        return self.exists

    async def get_collection(self, *, collection_name):
        self.collection_name = collection_name
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=self.vectors)))


@pytest.mark.asyncio
async def test_readiness_checks_the_configured_alias_and_vector_shape():
    client = _Client(SimpleNamespace(size=2048, distance=SimpleNamespace(value="Cosine")))

    await check_qdrant_collection(
        client,
        collection="argos_documents",
        vector_size=2048,
        distance="Cosine",
    )

    assert client.collection_name == "argos_documents"


@pytest.mark.asyncio
async def test_readiness_allows_a_clean_qdrant_before_the_first_authorized_sync():
    client = _Client(exists=False)

    await check_qdrant_collection(
        client,
        collection="argos_documents",
        vector_size=2048,
        distance="Cosine",
    )

    assert client.exists_collection_name == "argos_documents"
    assert client.collection_name is None


@pytest.mark.asyncio
async def test_readiness_still_fails_when_qdrant_is_unavailable():
    client = _Client(exists=False, error=ConnectionError("qdrant unavailable"))

    with pytest.raises(ConnectionError, match="qdrant unavailable"):
        await check_qdrant_collection(
            client,
            collection="argos_documents",
            vector_size=2048,
            distance="Cosine",
        )


@pytest.mark.asyncio
async def test_readiness_rejects_a_collection_with_an_old_vector_dimension():
    client = _Client(SimpleNamespace(size=64, distance=SimpleNamespace(value="Cosine")))

    with pytest.raises(RuntimeError, match="diverge"):
        await check_qdrant_collection(
            client,
            collection="argos_documents",
            vector_size=2048,
            distance="Cosine",
        )


@pytest.mark.asyncio
async def test_readiness_rejects_named_vectors_not_supported_by_the_reader_contract():
    client = _Client({"default": SimpleNamespace(size=2048, distance="Cosine")})

    with pytest.raises(RuntimeError, match="diverge"):
        await check_qdrant_collection(
            client,
            collection="argos_documents",
            vector_size=2048,
            distance="Cosine",
        )


@pytest.mark.asyncio
async def test_readiness_accepts_configured_openai_and_voyage_named_vectors():
    client = _Client(
        {
            "openai": SimpleNamespace(size=1536, distance=SimpleNamespace(value="Cosine")),
            "voyage": SimpleNamespace(size=1024, distance=SimpleNamespace(value="Cosine")),
        }
    )

    await check_qdrant_collection(
        client,
        collection="argos_documents",
        vector_size=1536,
        voyage_vector_size=1024,
        distance="Cosine",
    )

    assert client.collection_name == "argos_documents"
