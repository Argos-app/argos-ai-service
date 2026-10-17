from types import SimpleNamespace

import pytest
from argos_a2a_agent.infrastructure.qdrant_health import check_qdrant_collection


class _Client:
    def __init__(self, vectors):
        self.vectors = vectors
        self.collection_name = None

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
