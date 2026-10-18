"""Composição completa do runtime isolado do serviço A2A documental."""

import hashlib
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any

from argos_a2a_agent.adapters.authorization import PostgresDocumentPermissionDirectory
from argos_a2a_agent.adapters.documents.pypdf_parser import PyPdfParser
from argos_a2a_agent.adapters.providers import (
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleEmbeddingProvider,
)
from argos_a2a_agent.adapters.storage import LocalDocumentSource, LocalManifestRepository
from argos_a2a_agent.agents.factory import DocumentAgentFactory
from argos_a2a_agent.application.ensure_current_corpus import EnsureCurrentCorpus
from argos_a2a_agent.application.prepare_document import PrepareDocumentForIndex
from argos_a2a_agent.application.text_chunker import TextChunker
from argos_a2a_agent.infrastructure import (
    DocumentCorpusSynchronizer,
    QdrantDocumentIndexWriter,
    QdrantDocumentReadRepository,
    ReadOnlyQdrantReader,
)
from argos_a2a_agent.ports.document_answer_generator import DocumentAnswerGenerator
from argos_a2a_agent.ports.embedding_provider import EmbeddingProvider
from argos_a2a_agent.server import create_a2a_server
from argos_a2a_agent.settings import Settings
from packages.security.delegation import GlobalDocumentDelegationTokenVerifier
from packages.security.document_access import (
    DocumentAccessPolicy,
    DocumentPermissionDirectory,
)

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    """Portas concretas utilizadas pelo runtime e injetáveis em integrações."""

    embeddings: EmbeddingProvider
    embedding_fingerprint: str
    embedding_vector_size: int
    answer_generator: DocumentAnswerGenerator
    permission_directory: DocumentPermissionDirectory

    def validate(self) -> None:
        if not isinstance(self.embedding_fingerprint, str) or not _FINGERPRINT.fullmatch(
            self.embedding_fingerprint
        ):
            raise ValueError("embedding_fingerprint deve ser SHA-256 hexadecimal minúsculo")
        if (
            isinstance(self.embedding_vector_size, bool)
            or not isinstance(self.embedding_vector_size, int)
            or self.embedding_vector_size < 1
        ):
            raise ValueError("embedding_vector_size deve ser inteiro positivo")
        if not callable(getattr(self.embeddings, "embed", None)):
            raise ValueError("adapter de embeddings deve implementar embed(texts)")
        if not callable(getattr(self.answer_generator, "generate", None)):
            raise ValueError("gerador de respostas deve implementar generate(...)")
        if not callable(
            getattr(self.permission_directory, "has_active_documents_read_permission", None)
        ):
            raise ValueError("diretório vigente deve implementar a consulta documents.read")


@dataclass(slots=True)
class ServiceRuntime:
    """Aplicação A2A e callbacks privados dos recursos que o processo possui."""

    server: Any
    delegation_verifier: GlobalDocumentDelegationTokenVerifier
    _closers: tuple[Any, ...] = field(default=(), repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        for close in reversed(self._closers):
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("falha ao fechar recursos do runtime A2A", errors)


def _processing_fingerprint(embedding_fingerprint: str, chunker: TextChunker) -> str:
    config = {
        "embedding": embedding_fingerprint,
        "parser": "pypdf-strict-v1",
        "chunker": {
            "max_chars": chunker.fingerprint_config[0],
            "overlap_chars": chunker.fingerprint_config[1],
            "max_chunks": chunker.fingerprint_config[2],
        },
    }
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _close_resources(resources: list[Any]) -> None:
    errors: list[Exception] = []
    seen: set[int] = set()
    for resource in reversed(resources):
        if id(resource) in seen:
            continue
        seen.add(id(resource))
        close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("falha ao liberar recursos durante bootstrap", errors)


async def create_runtime_dependencies(settings: Settings) -> RuntimeDependencies:
    """Cria adapters reais usando APIs OpenAI-compatible e PostgreSQL read-only."""

    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeError("instale a dependência 'asyncpg' para consultar permissões") from exc

    pool = await asyncpg.create_pool(
        settings.permission_sql_dsn,
        min_size=1,
        max_size=10,
        command_timeout=5,
        server_settings={"default_transaction_read_only": "on"},
    )
    provider_resources: list[Any] = []
    try:
        async with pool.acquire() as connection:
            read_only = await connection.fetchval("SHOW default_transaction_read_only")
        if str(read_only).lower() != "on":
            raise RuntimeError("a conexão de autorização PostgreSQL não está somente leitura")

        embeddings = OpenAICompatibleEmbeddingProvider(
            settings.embeddings_api_url,
            settings.embeddings_api_key,
            settings.embeddings_model,
            settings.model_timeout_seconds,
        )
        provider_resources.append(embeddings)
        answer_generator = OpenAICompatibleAnswerGenerator(
            settings.answer_api_url,
            settings.answer_api_key,
            settings.answer_model,
            settings.model_timeout_seconds,
        )
        provider_resources.append(answer_generator)
        permission_directory = PostgresDocumentPermissionDirectory(
            pool,
            settings.documents_read_role_codes,
        )
        fingerprint_material = (
            f"openai-compatible:{settings.embeddings_model}:{settings.embeddings_model_revision}"
        )
        return RuntimeDependencies(
            embeddings=embeddings,
            embedding_fingerprint=hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest(),
            embedding_vector_size=settings.qdrant_vector_size,
            answer_generator=answer_generator,
            permission_directory=permission_directory,
        )
    except BaseException:
        await _close_resources(provider_resources)
        await pool.close()
        raise


async def create_runtime(
    settings: Settings | None = None,
    *,
    dependencies: RuntimeDependencies | None = None,
) -> ServiceRuntime:
    """Valida e conecta segurança, atualização, consulta, agentes e protocolo."""

    configured = settings or Settings.from_env()
    configured.validate_runtime(require_providers=dependencies is None)
    if configured.document_source_root is None or configured.manifest_path is None:
        raise ValueError("fonte local e caminho do manifesto são obrigatórios")
    source = LocalDocumentSource(
        configured.document_source_root,
        catalog_name=configured.document_catalog_name,
    )
    catalog_path = configured.document_source_root / configured.document_catalog_name
    if catalog_path.is_symlink() or not catalog_path.is_file():
        raise ValueError("catálogo allowlisted deve existir como arquivo regular")
    manifests = LocalManifestRepository(configured.manifest_path)
    # Detecta manifesto ilegível/corrompido antes de aceitar tráfego; ausência é
    # válida na primeira inicialização e será criada pelo sincronizador.
    await manifests.load()
    dependencies = dependencies or await create_runtime_dependencies(configured)
    resources: list[Any] = [
        dependencies.embeddings,
        dependencies.answer_generator,
        dependencies.permission_directory,
    ]
    try:
        dependencies.validate()
        if dependencies.embedding_vector_size != configured.qdrant_vector_size:
            raise ValueError("dimensão do adapter de embeddings diverge da configuração Qdrant")

        parser = PyPdfParser()
        chunker = TextChunker()
        processor = PrepareDocumentForIndex(parser, dependencies.embeddings, chunker)
        delegation_verifier = GlobalDocumentDelegationTokenVerifier(
            dict(configured.trusted_delegation_keys),
            issuer=configured.delegation_issuer,
            audience=configured.resource_server_url,
            max_ttl_seconds=configured.delegation_max_ttl_seconds,
            clock_skew_seconds=configured.delegation_clock_skew_seconds,
        )

        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as exc:
            raise RuntimeError(
                "instale a dependência 'qdrant-client' para iniciar o serviço"
            ) from exc

        reader_client = AsyncQdrantClient(
            url=configured.qdrant_url,
            api_key=configured.qdrant_read_api_key,
            timeout=configured.qdrant_timeout_seconds,
        )
        resources.append(reader_client)
        writer_client = AsyncQdrantClient(
            url=configured.qdrant_url,
            api_key=configured.qdrant_write_api_key,
            timeout=configured.qdrant_timeout_seconds,
        )
        resources.append(writer_client)
        await reader_client.get_collections()
        await writer_client.get_collections()

        reader = ReadOnlyQdrantReader(reader_client, configured.qdrant_collection)
        writer = QdrantDocumentIndexWriter(
            writer_client,
            active_alias=configured.qdrant_collection,
            vector_size=configured.qdrant_vector_size,
            distance=configured.qdrant_distance,
        )
        synchronizer = DocumentCorpusSynchronizer(
            source,
            manifests,
            processor,
            # O writer de credencial distinta só é alcançado pelo sincronizador.
            # Não é passado ao leitor ou ao executor A2A.
            writer,
            processing_fingerprint=_processing_fingerprint(
                dependencies.embedding_fingerprint, chunker
            ),
        )
        permission_policy = DocumentAccessPolicy(dependencies.permission_directory)
        ensure_current = EnsureCurrentCorpus(permission_policy, synchronizer)

        repository = QdrantDocumentReadRepository(reader, manifests)
        agent_registry = DocumentAgentFactory.create(
            ensure_current=ensure_current,
            repository=repository,
            embeddings=dependencies.embeddings,
            answer_generator=dependencies.answer_generator,
        )
        server = create_a2a_server(
            configured,
            readiness_check=reader_client.get_collections,
            delegation_verifier=delegation_verifier,
            agent_registry=agent_registry,
        )

        # Os adapters passam à propriedade do runtime depois que a composição tem êxito.
        closers = []
        seen: set[int] = set()
        for resource in resources:
            if id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
            if callable(close):
                closers.append(close)
        return ServiceRuntime(
            server=server,
            delegation_verifier=delegation_verifier,
            _closers=tuple(closers),
        )
    except BaseException:
        await _close_resources(resources)
        raise


async def run_server(
    settings: Settings | None = None,
    *,
    dependencies: RuntimeDependencies | None = None,
) -> None:
    import uvicorn

    configured = settings or Settings.from_env()
    runtime = await create_runtime(configured, dependencies=dependencies)
    try:
        config = uvicorn.Config(runtime.server, host=configured.a2a_host, port=configured.a2a_port)
        await uvicorn.Server(config).serve()
    finally:
        await runtime.close()
