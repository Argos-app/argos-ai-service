"""Homologação ponta a ponta do processo A2A sobre Qdrant real descartável."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from a2a.client import ClientConfig, create_client
from a2a.types import (
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    a2a_pb2,
)
from a2a.utils.errors import TaskNotFoundError
from argos_a2a_agent.adapters.inbound.a2a_server import create_agent_card
from argos_a2a_agent.adapters.storage import LocalManifestRepository
from argos_a2a_agent.application.document_queries import AggregateDocuments, GetDocumentExcerpt
from argos_a2a_agent.application.ensure_current_corpus import EnsureCurrentCorpusOutcome
from argos_a2a_agent.infrastructure import QdrantDocumentReadRepository, ReadOnlyQdrantReader
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import DeleteAlias, DeleteAliasOperation

from packages.contracts.a2a import CorpusRefreshStatus
from packages.contracts.documents import (
    AggregateDocumentsRequest,
    DocumentGroupDimension,
    GetDocumentExcerptRequest,
)
from packages.security.access import DocumentAccessContext

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("A2A_RUN_INTEGRATION") != "1",
        reason="suba o compose e defina A2A_RUN_INTEGRATION=1",
    ),
]

ROOT = Path(__file__).resolve().parents[4]
APP_SRC = ROOT / "apps/a2a_agent/src"
FIXTURES = ROOT / "apps/a2a_agent/data/documents"
SERVER_SCRIPT = ROOT / "apps/a2a_agent/tests/integration/a2a_runtime_server.py"
QDRANT_URL = os.getenv("A2A_TEST_QDRANT_URL", "http://127.0.0.1:6338")
WRITE_KEY = os.getenv("A2A_TEST_QDRANT_WRITE_KEY", "a2a-integration-write-key")
READ_KEY = os.getenv("A2A_TEST_QDRANT_READ_KEY", "a2a-integration-read-key")
SERVICE_TOKEN = "integration-service-token"
ISSUER = "a2a-integration-issuer"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class A2AHarness:
    alias: str
    source_root: Path
    manifest_path: Path
    permission_file: Path
    endpoint: str
    user_id: UUID
    private_key: Ed25519PrivateKey
    process: subprocess.Popen
    log_path: Path
    read_client: AsyncQdrantClient
    write_client: AsyncQdrantClient
    log_file: object

    def delegation(self, *, user_id: UUID | None = None, **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": self.endpoint,
            "sub": str(user_id or self.user_id),
            "agent": "informacoes",
            "operations": ["documents.read"],
            "iat": now,
            "nbf": now,
            "exp": now + 120,
            "v": 1,
            "jti": str(uuid4()),
            "request_id": str(uuid4()),
        }
        claims.update(overrides)
        encoded_header = json.dumps(
            {"alg": "EdDSA", "kid": "integration-key"}, separators=(",", ":")
        ).encode()
        header = _b64url(encoded_header)
        payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        return f"{header}.{payload}.{_b64url(self.private_key.sign(signing_input))}"

    def set_authorized(self, users: list[UUID]) -> None:
        self.permission_file.write_text(json.dumps([str(user) for user in users]), encoding="utf-8")

    async def sdk_client(self, *, user_id: UUID | None = None, delegation: str | None = None):
        headers = {
            "Authorization": f"Bearer {SERVICE_TOKEN}",
            "X-Argos-Delegation": delegation or self.delegation(user_id=user_id),
        }
        http = httpx.AsyncClient(headers=headers, timeout=5)
        card = create_agent_card(self.endpoint)
        client = await create_client(
            card,
            client_config=ClientConfig(streaming=False, httpx_client=http),
        )
        return client, http

    async def ask(
        self,
        *,
        skill: str,
        question: str,
        user_id: UUID | None = None,
        document_ids: tuple[str, ...] | None = None,
        jurisdiction: str | None = None,
    ):
        client, http = await self.sdk_client(user_id=user_id)
        request = {
            "request_id": str(uuid4()),
            "question": question,
            "skill": skill,
            "deadline_at": datetime.fromtimestamp(time.time() + 30, UTC).isoformat(),
        }
        if document_ids is not None:
            request["document_ids"] = list(document_ids)
        if jurisdiction is not None:
            request["jurisdiction"] = jurisdiction
        message = Message(
            message_id=str(uuid4()),
            role=Role.ROLE_USER,
            parts=[Part(text=json.dumps(request), media_type="application/json")],
        )
        rpc = SendMessageRequest(
            message=message,
            configuration=SendMessageConfiguration(accepted_output_modes=["application/json"]),
        )
        events = [item async for item in client.send_message(rpc)]
        await client.close()
        await http.aclose()
        tasks = [item.task for item in events if item.task is not None]
        assert tasks, f"SDK não recebeu task do servidor: {events!r}"
        task = tasks[-1]
        artifact_text = task.artifacts[-1].parts[0].text
        return json.loads(artifact_text), task

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_file.close()

    def start(self, tmp_path: Path) -> None:
        if self.process.poll() is None:
            return
        self.log_file = self.log_path.open("a", encoding="utf-8")
        self.process = _spawn_server(self, tmp_path)


def _spawn_server(harness: A2AHarness, tmp_path: Path) -> subprocess.Popen:
    public_key = harness.private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.pathsep.join((str(APP_SRC), str(ROOT), env.get("PYTHONPATH", ""))),
            "A2A_QDRANT_URL": QDRANT_URL,
            "A2A_QDRANT_READ_API_KEY": READ_KEY,
            "A2A_QDRANT_WRITE_API_KEY": WRITE_KEY,
            "A2A_QDRANT_COLLECTION": harness.alias,
            "A2A_QDRANT_VECTOR_SIZE": "3",
            "A2A_QDRANT_DISTANCE": "Cosine",
            "A2A_DOCUMENT_SOURCE_ROOT": str(harness.source_root),
            "A2A_MANIFEST_PATH": str(harness.manifest_path),
            "A2A_INTERNAL_SERVICE_TOKEN": SERVICE_TOKEN,
            "A2A_RESOURCE_SERVER_URL": harness.endpoint,
            "A2A_DELEGATION_ISSUER": ISSUER,
            "A2A_TRUSTED_DELEGATION_KEYS": json.dumps(
                {"integration-key": base64.b64encode(public_key).decode("ascii")}
            ),
            "A2A_HOST": "127.0.0.1",
            "A2A_PORT": str(int(harness.endpoint.rsplit(":", 1)[1].split("/", 1)[0])),
            "A2A_ENDPOINT_URL": harness.endpoint,
            "A2A_TEST_PERMISSION_FILE": str(harness.permission_file),
        }
    )
    return subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        cwd=ROOT,
        env=env,
        stdout=harness.log_file,
        stderr=subprocess.STDOUT,
    )


async def _wait_ready(harness: A2AHarness) -> None:
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    async with httpx.AsyncClient(headers=headers, timeout=1) as client:
        for _ in range(100):
            if harness.process.poll() is not None:
                break
            try:
                response = await client.get(harness.endpoint.removesuffix("/a2a") + "/health/ready")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    detail = harness.log_path.read_text(encoding="utf-8", errors="replace")
    pytest.fail(f"servidor A2A não ficou pronto. Log: {detail}")


@pytest_asyncio.fixture
async def a2a_harness(tmp_path: Path):
    read_client = AsyncQdrantClient(url=QDRANT_URL, api_key=READ_KEY, timeout=3)
    write_client = AsyncQdrantClient(url=QDRANT_URL, api_key=WRITE_KEY, timeout=3)
    alias = f"a2a_e2e_{os.urandom(8).hex()}"
    source_root = tmp_path / "source"
    import shutil

    shutil.copytree(FIXTURES, source_root)
    permission_file = tmp_path / "permissions.json"
    user_id = uuid4()
    permission_file.write_text(json.dumps([str(user_id)]), encoding="utf-8")
    endpoint = f"http://127.0.0.1:{_free_port()}/a2a"
    private_key = Ed25519PrivateKey.generate()
    log_path = tmp_path / "a2a-server.log"
    harness = A2AHarness(
        alias=alias,
        source_root=source_root,
        manifest_path=tmp_path / "manifest.json",
        permission_file=permission_file,
        endpoint=endpoint,
        user_id=user_id,
        private_key=private_key,
        process=None,
        log_path=log_path,
        read_client=read_client,
        write_client=write_client,
        log_file=log_path.open("w", encoding="utf-8"),
    )
    harness.process = _spawn_server(harness, tmp_path)
    try:
        await _wait_ready(harness)
        yield harness
    finally:
        harness.stop()
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


@pytest.mark.asyncio
async def test_sdk_queries_two_agents_mixed_refresh_restart_and_concurrency(
    a2a_harness: A2AHarness, tmp_path: Path
) -> None:
    harness = a2a_harness
    law_id = "11111111-1111-4111-8111-111111111111"
    terms_id = "22222222-2222-4222-8222-222222222222"

    legal, _ = await harness.ask(
        skill="legal", question="O que diz a lei?", document_ids=(law_id,), jurisdiction="BR"
    )
    terms, _ = await harness.ask(
        skill="documental", question="Quais são as condições?", document_ids=(terms_id,)
    )
    combined, _ = await harness.ask(
        skill="mixed",
        question="Compare os termos e as leis",
        document_ids=(law_id, terms_id),
        jurisdiction="BR",
    )
    assert legal["status"] == terms["status"] == combined["status"] == "success"
    assert {source["document_type"] for source in legal["sources"]} == {"law"}
    assert {source["document_type"] for source in terms["sources"]} == {"terms_conditions"}
    assert {source["document_type"] for source in combined["sources"]} == {
        "law",
        "terms_conditions",
    }
    initial_revision = combined["corpus_revision"]

    # Chamadas simultâneas sobre corpus já publicado não devem misturar tasks ou fontes.
    parallel = await asyncio.gather(
        harness.ask(
            skill="legal",
            question="Consulta simultânea A",
            document_ids=(law_id,),
            jurisdiction="BR",
        ),
        harness.ask(
            skill="documental",
            question="Consulta simultânea B",
            document_ids=(terms_id,),
        ),
    )
    assert all(answer["status"] == "success" for answer, _ in parallel)
    assert {source["document_type"] for source in parallel[0][0]["sources"]} == {"law"}
    assert {source["document_type"] for source in parallel[1][0]["sources"]} == {"terms_conditions"}

    catalog_path = harness.source_root / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["documents"][0]["title"] = "Lei global atualizada em homologação"
    catalog["documents"][0]["source_modified_at"] = "2026-10-10T12:00:00+00:00"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    refreshed, _ = await harness.ask(
        skill="legal",
        question="Confirme a revisão atual",
        document_ids=(law_id,),
        jurisdiction="BR",
    )
    assert refreshed["status"] == "success"
    assert refreshed["corpus_revision"] != initial_revision
    assert any(
        source["title"] == "Lei global atualizada em homologação" for source in refreshed["sources"]
    )

    harness.stop()
    harness.start(tmp_path)
    await _wait_ready(harness)
    restarted, _ = await harness.ask(
        skill="legal", question="Confirme após reinício", document_ids=(law_id,), jurisdiction="BR"
    )
    assert restarted["status"] == "success"
    assert restarted["corpus_revision"] == refreshed["corpus_revision"]
    assert any(
        source["title"] == "Lei global atualizada em homologação" for source in restarted["sources"]
    )


@pytest.mark.asyncio
async def test_main_api_a2a_client_records_its_own_observation(a2a_harness: A2AHarness) -> None:
    """O cliente do main_api registra sua chamada separadamente da medição do servidor."""
    from app.infrastructure.a2a.client import DocumentA2AClient
    from app.settings import Settings as MainAPISettings

    from packages.observability import ObservationStatus

    harness = a2a_harness
    private_der = harness.private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    settings = MainAPISettings(
        mcp_delegation_issuer=ISSUER,
        mcp_delegation_key_id="integration-key",
        mcp_delegation_private_key=base64.b64encode(private_der).decode("ascii"),
        document_a2a_url=harness.endpoint,
        document_a2a_service_token=SERVICE_TOKEN,
    )

    class Collector:
        def __init__(self) -> None:
            self.observations = []

        def emit(self, observation) -> None:
            self.observations.append(observation)

    collector = Collector()
    client = DocumentA2AClient(settings, observation_collector=collector)
    question = "Quais são as condições?"
    try:
        result = json.loads(await client.ask(question, user_id=harness.user_id))
    finally:
        await client.aclose()

    assert result["status"] in {"success", "unavailable"}
    assert len(collector.observations) == 2
    started, terminal = collector.observations
    assert started.status == ObservationStatus.STARTED
    assert terminal.status == ObservationStatus.SUCCEEDED
    assert terminal.operation_name == "a2a.documental.message.send"
    assert terminal.agent == "documental"
    assert terminal.protocol.name == "a2a"
    assert terminal.protocol.operation == "message.send"
    assert terminal.protocol.skill == "documental"
    assert terminal.protocol.task_state == "completed"
    assert terminal.result.code == result["status"]
    assert terminal.started_at.tzinfo is not None
    assert terminal.ended_at.tzinfo is not None
    assert terminal.duration_ms is not None and terminal.duration_ms >= 0
    serialized = repr(terminal.model_dump())
    assert question not in serialized
    assert str(harness.user_id) not in serialized


@pytest.mark.asyncio
async def test_detailed_and_aggregated_read_use_cases_over_published_corpus(
    a2a_harness: A2AHarness,
) -> None:
    harness = a2a_harness
    answer, _ = await harness.ask(
        skill="legal",
        question="Publique e consulte a lei",
        document_ids=("11111111-1111-4111-8111-111111111111",),
        jurisdiction="BR",
    )
    corpus_revision = answer["corpus_revision"]
    document_revision = answer["sources"][0]["revision_hash"]

    class CurrentRevision:
        async def execute(self, context):
            assert context.user_id == harness.user_id
            return EnsureCurrentCorpusOutcome(
                status=CorpusRefreshStatus.UNCHANGED,
                checked_at=datetime.now(UTC),
                corpus_revision=corpus_revision,
            )

        async def reauthorize(self, context):
            assert context.user_id == harness.user_id

    repository = QdrantDocumentReadRepository(
        ReadOnlyQdrantReader(harness.read_client, harness.alias),
        LocalManifestRepository(harness.manifest_path),
    )
    now = datetime.now(UTC)
    context = DocumentAccessContext(
        user_id=harness.user_id,
        operations=frozenset({"documents.read"}),
        agent_id="informacoes",
        issued_at=now,
        expires_at=datetime.fromtimestamp(now.timestamp() + 60, UTC),
        token_id=str(uuid4()),
        request_id=str(uuid4()),
    )

    excerpt = await GetDocumentExcerpt(CurrentRevision(), repository).execute(
        GetDocumentExcerptRequest(
            document_id=UUID("11111111-1111-4111-8111-111111111111"),
            page_from=1,
            page_to=1,
            expected_revision=document_revision,
        ),
        context,
    )
    assert excerpt.source.document_type.value == "law"
    assert excerpt.source.page_from == excerpt.source.page_to == 1
    assert excerpt.source.excerpt

    aggregate = await AggregateDocuments(CurrentRevision(), repository).execute(
        AggregateDocumentsRequest(group_by=(DocumentGroupDimension.DOCUMENT_TYPE,)),
        context,
    )
    assert aggregate.complete
    assert aggregate.count_distinct_documents == 2
    assert {(group.dimensions[0].value, group.document_count) for group in aggregate.groups} == {
        ("law", 1),
        ("terms_conditions", 1),
    }


@pytest.mark.asyncio
async def test_a2a_requires_service_and_delegation_authentication(a2a_harness: A2AHarness) -> None:
    harness = a2a_harness
    async with httpx.AsyncClient(timeout=3) as client:
        no_auth = await client.get(harness.endpoint)
        assert no_auth.status_code == 401
        bad_service = await client.get(harness.endpoint, headers={"Authorization": "Bearer wrong"})
        assert bad_service.status_code == 401
        no_delegation = await client.get(
            harness.endpoint, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}
        )
        assert no_delegation.status_code == 401
        bad_delegation = await client.get(
            harness.endpoint,
            headers={
                "Authorization": f"Bearer {SERVICE_TOKEN}",
                "X-Argos-Delegation": "not.a.valid-token",
            },
        )
        assert bad_delegation.status_code == 401
        invalid_context = await client.get(
            harness.endpoint,
            headers={
                "Authorization": f"Bearer {SERVICE_TOKEN}",
                "X-Argos-Delegation": harness.delegation(operations=["documents.write"]),
            },
        )
        assert invalid_context.status_code == 401


@pytest.mark.asyncio
async def test_global_contract_rejects_property_scope(a2a_harness: A2AHarness) -> None:
    """O corpus é global: o contrato não aceita filtros de propriedade."""
    harness = a2a_harness
    client, http = await harness.sdk_client()
    request = {
        "request_id": str(uuid4()),
        "question": "Não deve aceitar filtro de propriedade",
        "skill": "legal",
        "document_ids": ["11111111-1111-4111-8111-111111111111"],
        "jurisdiction": "BR",
        "property_id": str(uuid4()),
        "deadline_at": datetime.fromtimestamp(time.time() + 30, UTC).isoformat(),
    }
    rpc = SendMessageRequest(
        message=Message(
            message_id=str(uuid4()),
            role=Role.ROLE_USER,
            parts=[Part(text=json.dumps(request), media_type="application/json")],
        ),
        configuration=SendMessageConfiguration(accepted_output_modes=["application/json"]),
    )
    events = [item async for item in client.send_message(rpc)]
    tasks = [item.task for item in events if item.task is not None]
    assert tasks
    assert tasks[-1].status.state == a2a_pb2.TASK_STATE_REJECTED
    assert not tasks[-1].artifacts
    await client.close()
    await http.aclose()


@pytest.mark.asyncio
async def test_revocation_and_task_ownership_are_rechecked_per_request(
    a2a_harness: A2AHarness,
) -> None:
    harness = a2a_harness
    answer, task = await harness.ask(
        skill="legal",
        question="Consulta autorizada",
        document_ids=("11111111-1111-4111-8111-111111111111",),
        jurisdiction="BR",
    )
    assert answer["status"] == "success"
    assert task.id

    other_user = uuid4()
    harness.set_authorized([harness.user_id, other_user])
    other_client, other_http = await harness.sdk_client(user_id=other_user)
    with pytest.raises(TaskNotFoundError):
        await other_client.get_task(GetTaskRequest(id=task.id))
    await other_client.close()
    await other_http.aclose()

    harness.set_authorized([other_user])
    revoked, _ = await harness.ask(
        skill="legal",
        question="Esta consulta deve ser negada pela permissão vigente",
        document_ids=("11111111-1111-4111-8111-111111111111",),
        jurisdiction="BR",
    )
    assert revoked["status"] == "unavailable"
    assert revoked["sources"] == []
