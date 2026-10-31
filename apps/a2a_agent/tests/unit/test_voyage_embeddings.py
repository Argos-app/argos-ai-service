"""Contrato HTTP dos embeddings Voyage usados pelo índice de contingência."""

from argos_a2a_agent.adapters.providers import embeddings as embedding_adapters
from argos_a2a_agent.adapters.providers.embeddings import (
    OpenAICompatibleEmbeddingProvider,
    VoyageEmbeddingProvider,
)
from argos_a2a_agent.infrastructure.qdrant import ReadOnlyQdrantReader


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("o adapter fake não deve ceder controle de I/O")


class _FakeHTTP:
    def __init__(self, endpoint, api_key, timeout_seconds):
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.payload = None

    async def post_json(
        self, payload, *, recorder=None, operation_route="provider", rate_profile=None
    ):
        self.rate_profile = rate_profile
        self.payload = payload
        return {
            "model": payload["model"],
            "usage": {"total_tokens": 12},
            "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
        }

    async def aclose(self):
        return None


def test_voyage_adapter_sends_query_type_and_uses_native_model_id(monkeypatch):
    monkeypatch.setattr(embedding_adapters, "OpenAICompatibleHTTP", _FakeHTTP)
    provider = VoyageEmbeddingProvider(
        "voyage-secret",
        "voyage-4-large",
        10,
        endpoint="https://voyage-gateway.example/v1/embeddings",
    )

    vectors = run_immediate(
        OpenAICompatibleEmbeddingProvider.embed.__wrapped__(
            provider, ("pergunta",), input_type="query"
        )
    )

    assert provider._http.endpoint == "https://voyage-gateway.example/v1/embeddings"
    assert provider._http.api_key == "voyage-secret"
    assert provider._http.payload == {
        "model": "voyage-4-large",
        "input": ["pergunta"],
        "input_type": "query",
    }
    assert provider._http.rate_profile == "embedding_fallback"
    assert vectors == ((0.1, 0.2, 0.3),)


def test_voyage_adapter_accepts_custom_embedding_endpoint(monkeypatch):
    monkeypatch.setattr(embedding_adapters, "OpenAICompatibleHTTP", _FakeHTTP)
    provider = VoyageEmbeddingProvider(
        "voyage-secret",
        "voyage-4-large",
        10,
        endpoint="https://voyage-proxy.example/v1/embeddings",
    )

    assert provider._http.endpoint == "https://voyage-proxy.example/v1/embeddings"


def test_voyage_adapter_marks_document_inputs_for_indexing(monkeypatch):
    monkeypatch.setattr(embedding_adapters, "OpenAICompatibleHTTP", _FakeHTTP)
    provider = VoyageEmbeddingProvider(
        "voyage-secret",
        "voyage-4-large",
        10,
        endpoint="https://voyage-gateway.example/v1/embeddings",
    )

    run_immediate(
        OpenAICompatibleEmbeddingProvider.embed.__wrapped__(
            provider, ("texto do documento",), input_type="document"
        )
    )

    assert provider._http.payload["input_type"] == "document"


def test_qdrant_reader_selects_voyage_named_vector():
    class _FakeQdrant:
        def __init__(self):
            self.query = None

        async def query_points(self, **kwargs):
            self.query = kwargs
            return type("Result", (), {"points": ()})()

    client = _FakeQdrant()
    reader = ReadOnlyQdrantReader(client, "documents")
    query_filter = {"must": [{"key": "corpus_revision", "match": {"value": "a" * 64}}]}

    run_immediate(
        reader.query_points(
            (0.1, 0.2),
            query_filter=query_filter,
            limit=3,
            vector_name="voyage",
        )
    )

    assert client.query["using"] == "voyage"
