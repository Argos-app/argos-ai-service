"""Composição completa do runtime isolado do serviço A2A documental."""

import hashlib
import inspect
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from argos_a2a_agent.adapters.authorization import PostgresDocumentPermissionDirectory
from argos_a2a_agent.adapters.documents.pypdf_parser import PyPdfParser
from argos_a2a_agent.adapters.observability import (
    MongoObservationRepository,
    MongoObservationWriter,
    observe_adapter,
)
from argos_a2a_agent.adapters.observability.langsmith import create_langsmith_tracing
from argos_a2a_agent.adapters.providers import (
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleEmbeddingProvider,
    SimulatedAnswerGenerator,
    SimulatedEmbeddingProvider,
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
from packages.observability import (
    BestEffortObservationCollector,
    DisabledObservationCollector,
    ObservationCollector,
)
from packages.observability.redis_user_usage import RedisUserUsageCounter
from packages.security.delegation import GlobalDocumentDelegationTokenVerifier
from packages.security.document_access import (
    DocumentAccessPolicy,
    DocumentPermissionDirectory,
)

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_LOGGER = logging.getLogger(__name__)


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
    observation_collector: ObservationCollector
    observability_writer: MongoObservationWriter | None = None
    _closers: tuple[Any, ...] = field(default=(), repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        if self.observability_writer is not None:
            try:
                await self.observability_writer.close()
            except Exception as exc:
                errors.append(exc)
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


async def create_runtime_dependencies(
    settings: Settings,
    *,
    observation_collector: ObservationCollector | None = None,
) -> RuntimeDependencies:
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

        permission_directory = PostgresDocumentPermissionDirectory(
            pool,
            observation_collector=observation_collector,
        )
        if settings.model_mode == "simulated":
            embeddings = SimulatedEmbeddingProvider(settings.qdrant_vector_size)
            answer_generator = SimulatedAnswerGenerator()
            fingerprint_material = f"argos-simulated-embedding-v1:{settings.qdrant_vector_size}"
            return RuntimeDependencies(
                embeddings=embeddings,
                embedding_fingerprint=hashlib.sha256(
                    fingerprint_material.encode("utf-8")
                ).hexdigest(),
                embedding_vector_size=settings.qdrant_vector_size,
                answer_generator=answer_generator,
                permission_directory=permission_directory,
            )

        embeddings = OpenAICompatibleEmbeddingProvider(
            settings.embeddings_api_url,
            settings.embeddings_api_key,
            settings.embeddings_model,
            settings.model_timeout_seconds,
            observation_collector=observation_collector,
        )
        provider_resources.append(embeddings)
        answer_generator = OpenAICompatibleAnswerGenerator(
            settings.answer_api_url,
            settings.answer_api_key,
            settings.answer_model,
            settings.model_timeout_seconds,
            max_output_tokens=settings.model_max_output_tokens,
            observation_collector=observation_collector,
        )
        provider_resources.append(answer_generator)
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
    configured.validate_runtime(
        require_providers=dependencies is None and configured.model_mode != "simulated",
        require_permission_sql=dependencies is None,
    )
    if configured.document_source_root is None or configured.manifest_path is None:
        raise ValueError("fonte local e caminho do manifesto são obrigatórios")
    raw_source = LocalDocumentSource(
        configured.document_source_root,
        catalog_name=configured.document_catalog_name,
    )
    catalog_path = configured.document_source_root / configured.document_catalog_name
    if catalog_path.is_symlink() or not catalog_path.is_file():
        raise ValueError("catálogo allowlisted deve existir como arquivo regular")
    (
        observation_client,
        observation_writer,
        user_usage_counter,
        observation_collector,
    ) = await _create_observability_runtime(configured)
    langsmith_tracing = create_langsmith_tracing(configured)
    resources: list[Any] = []
    source = observe_adapter(
        raw_source,
        observation_collector,
        operation_prefix="documents.source",
    )
    manifests = observe_adapter(
        LocalManifestRepository(configured.manifest_path),
        observation_collector,
        operation_prefix="documents.manifest",
    )
    # Detecta manifesto ilegível/corrompido antes de aceitar tráfego; ausência é
    # válida na primeira inicialização e será criada pelo sincronizador.
    try:
        await manifests.load()
        dependencies = dependencies or await create_runtime_dependencies(
            configured,
            observation_collector=observation_collector,
        )
        resources = [
            dependencies.embeddings,
            dependencies.answer_generator,
            dependencies.permission_directory,
        ]
        dependencies.validate()
        if dependencies.embedding_vector_size != configured.qdrant_vector_size:
            raise ValueError("dimensão do adapter de embeddings diverge da configuração Qdrant")

        parser = observe_adapter(
            PyPdfParser(),
            observation_collector,
            operation_prefix="documents.pdf_parser",
        )
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

        reader = observe_adapter(
            ReadOnlyQdrantReader(reader_client, configured.qdrant_collection),
            observation_collector,
            operation_prefix="qdrant.reader",
        )
        writer = observe_adapter(
            QdrantDocumentIndexWriter(
                writer_client,
                active_alias=configured.qdrant_collection,
                vector_size=configured.qdrant_vector_size,
                distance=configured.qdrant_distance,
            ),
            observation_collector,
            operation_prefix="qdrant.index_writer",
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
            observation_collector=observation_collector,
        )
        server = create_a2a_server(
            configured,
            readiness_check=reader_client.get_collections,
            delegation_verifier=delegation_verifier,
            agent_registry=agent_registry,
            observation_collector=observation_collector,
            langsmith_tracing=langsmith_tracing,
        )

        # Os adapters passam à propriedade do runtime depois que a composição tem êxito.
        closers = [observation_client.close] if observation_client is not None else []
        if user_usage_counter is not None:
            closers.append(user_usage_counter.close)
        if langsmith_tracing is not None:
            closers.append(langsmith_tracing.close)
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
            observation_collector=observation_collector,
            observability_writer=observation_writer,
            _closers=tuple(closers),
        )
    except BaseException:
        if observation_writer is not None:
            await observation_writer.close()
        if observation_client is not None:
            resources.append(observation_client)
        if user_usage_counter is not None:
            resources.append(user_usage_counter)
        if langsmith_tracing is not None:
            resources.append(langsmith_tracing)
        await _close_resources(resources)
        raise


async def _create_observability_runtime(
    settings: Settings,
) -> tuple[
    Any | None,
    MongoObservationWriter | None,
    RedisUserUsageCounter | None,
    ObservationCollector,
]:
    if settings.observability_mongo_uri is None or settings.observability_collection is None:
        return None, None, None, DisabledObservationCollector()
    client = None
    writer = None
    user_usage_counter = None
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        from pymongo.write_concern import WriteConcern

        client = AsyncIOMotorClient(
            settings.observability_mongo_uri,
            tz_aware=True,
            serverSelectionTimeoutMS=settings.observability_mongo_server_selection_timeout_ms,
        )
        database = client.get_database(
            settings.observability_mongo_database,
            write_concern=WriteConcern(
                w="majority",
                wtimeout=settings.observability_mongo_write_timeout_ms,
            ),
        )
        redis_url = os.getenv("A2A_OBSERVABILITY_REDIS_URL")
        if redis_url:
            try:
                from redis.asyncio import Redis

                user_usage_counter = RedisUserUsageCounter(
                    Redis.from_url(
                        redis_url,
                        decode_responses=False,
                        socket_connect_timeout=1,
                        socket_timeout=1,
                    ),
                    key_prefix=os.getenv("USER_USAGE_REDIS_KEY_PREFIX", "argos:usage:users:v1"),
                )
            except Exception:
                _LOGGER.warning("A2A Redis user usage counters could not be configured")
        writer = MongoObservationWriter(
            MongoObservationRepository(database[settings.observability_collection]),
            user_usage_counter=user_usage_counter,
            max_items=settings.observability_queue_max_items,
            max_bytes=settings.observability_queue_max_bytes,
            max_document_bytes=settings.observability_document_max_bytes,
            batch_size=settings.observability_batch_size,
            batch_interval_ms=settings.observability_batch_interval_ms,
            max_attempts=settings.observability_max_attempts,
            retry_base_delay_ms=settings.observability_retry_base_delay_ms,
            shutdown_timeout_ms=settings.observability_shutdown_timeout_ms,
        )
        await writer.start()
        return client, writer, user_usage_counter, BestEffortObservationCollector(writer)
    except Exception:
        _LOGGER.warning("A2A observability persistence could not start")
        if writer is not None:
            await writer.close()
        if client is not None:
            client.close()
        if user_usage_counter is not None:
            await user_usage_counter.close()
        return None, None, None, DisabledObservationCollector()


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
