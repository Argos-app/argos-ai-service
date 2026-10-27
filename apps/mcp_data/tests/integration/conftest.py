"""Harness descartável para chamadas MCP reais contra o schema local do projeto."""

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest_asyncio

ROOT = Path(__file__).resolve().parents[4]
SQL_ADMIN_DSN = "postgresql://mcp_data_test:mcp_data_test_local_only@127.0.0.1:54329/mcp_data_test"
MONGO_URI = "mongodb://127.0.0.1:27029"
SCHEMA_MODULES = tuple(
    ROOT / "infrastructure" / "docker" / "postgres" / "init" / f"{index:02d}_{name}.sql"
    for index, name in enumerate(
        (
            "configuracao",
            "tabelas",
            "dados_iniciais",
            "indices",
            "funcoes",
            "triggers",
            "procedures",
            "views",
            "data_mart",
        ),
        start=1,
    )
)


def _schema_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class McpDataHarness:
    def __init__(self, database_name: str, private_key, fixture_ids: dict[str, object]) -> None:
        self.database_name = database_name
        self.private_key = private_key
        self.fixture_ids = fixture_ids
        self.service_token = uuid4().hex
        self.log_path = Path(os.getenv("TEMP", ".")) / f"{database_name}-mcp.log"
        self.process = None
        self.log_file = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"

    def delegation_token(self, overrides: dict[str, object] | None = None) -> str:
        now = int(datetime.now(UTC).timestamp())
        claims = {
            "v": 1,
            "iss": "argos-main-service-test",
            "aud": self.url,
            "sub": self.fixture_ids["firebase_uid"],
            "authorized_property_ids": [
                str(self.fixture_ids["property_a"]),
                str(self.fixture_ids["property_b"]),
            ],
            "operations": ["cattle.read"],
            "agent": "fazenda",
            "iat": now,
            "nbf": now,
            "exp": now + 240,
            "jti": uuid4().hex,
            "request_id": uuid4().hex,
        }
        claims.update(overrides or {})
        header = _b64url(json.dumps({"alg": "EdDSA", "kid": "integration-key"}).encode())
        payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        signature = _b64url(self.private_key.sign(signing_input))
        return f"{header}.{payload}.{signature}"

    async def start(self) -> None:
        import httpx
        from cryptography.hazmat.primitives import serialization

        public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        private_key_der = self.private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        env = dict(os.environ)
        env.update(
            PYTHONPATH=os.pathsep.join((str(ROOT / "apps/mcp_data/src"), str(ROOT))),
            PYTHONUNBUFFERED="1",
            MCP_DATA_SQL_DSN=SQL_ADMIN_DSN.rsplit("/", 1)[0] + "/" + self.database_name,
            MCP_DATA_MONGO_URI=MONGO_URI,
            MCP_DATA_MONGO_DATABASE=self.database_name,
            MCP_DATA_MONGO_RELATIONAL_ID_FORMAT="string",
            MCP_DATA_MONGO_MEDICINE_ID_FORMAT="string",
            MCP_DATA_INTERNAL_SERVICE_TOKEN=self.service_token,
            MCP_DATA_AUTH_ISSUER_URL=f"http://127.0.0.1:{self.port}/test-issuer",
            MCP_DATA_RESOURCE_SERVER_URL=self.url,
            MCP_DATA_DELEGATION_ISSUER="argos-main-service-test",
            MCP_DATA_TRUSTED_DELEGATION_KEYS=json.dumps(
                {"integration-key": base64.b64encode(public_key).decode("ascii")}
            ),
            MCP_DATA_REPORT_SIGNING_KEY_ID="integration-key",
            MCP_DATA_REPORT_SIGNING_PRIVATE_KEY=base64.b64encode(private_key_der).decode("ascii"),
            MCP_DATA_HOST="127.0.0.1",
            MCP_DATA_PORT=str(self.port),
        )
        self.log_file = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "argos_mcp_data"],
            cwd=ROOT,
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
            for _ in range(300):
                if self.process.poll() is not None:
                    raise RuntimeError(self.log_path.read_text(encoding="utf-8"))
                try:
                    response = await client.get(self.url)
                    if response.status_code == 401:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
        raise RuntimeError("MCP de dados não iniciou em 30s: " + self.log_path.read_text())

    async def stop(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    await asyncio.to_thread(self.process.wait, 10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    await asyncio.to_thread(self.process.wait, 5)
            self.process = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    async def connect_to_test_database(self):
        import asyncpg

        dsn = SQL_ADMIN_DSN.rsplit("/", 1)[0] + "/" + self.database_name
        return await asyncpg.connect(dsn, timeout=5)

    async def create_read_only_test_pool(self):
        import asyncpg

        dsn = SQL_ADMIN_DSN.rsplit("/", 1)[0] + "/" + self.database_name
        return await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=1,
            server_settings={"default_transaction_read_only": "on"},
        )

    @asynccontextmanager
    async def client(self, *, delegation_overrides: dict[str, object] | None = None):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.service_token}",
                "X-Argos-Delegation": self.delegation_token(delegation_overrides),
            },
            timeout=httpx.Timeout(30, read=120),
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as streams:
                read, write, _ = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def mcp_data_harness():
    import asyncpg
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from motor.motor_asyncio import AsyncIOMotorClient

    database_name = "mcp_data_it_" + uuid4().hex
    mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    admin = await asyncpg.connect(SQL_ADMIN_DSN, timeout=5)
    harness = None
    try:
        await mongo.admin.command("ping")
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        database_dsn = SQL_ADMIN_DSN.rsplit("/", 1)[0] + "/" + database_name
        sql = await asyncpg.connect(database_dsn, timeout=10)
        try:
            for module in SCHEMA_MODULES:
                await sql.execute(_schema_sql(module), timeout=180)
            fixture_ids = await _insert_synthetic_data(sql)
        finally:
            await sql.close()

        await _insert_mongo_documents(mongo[database_name], fixture_ids)

        harness = McpDataHarness(database_name, Ed25519PrivateKey.generate(), fixture_ids)
        await harness.start()
        yield harness
    finally:
        if harness is not None:
            await harness.stop()
        await mongo.drop_database(database_name)
        mongo.close()
        try:
            await admin.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _insert_mongo_documents(database, fixture_ids: dict[str, object]) -> None:
    """Insere documentos sintéticos independentes das linhas relacionais."""
    now = datetime.now(UTC)
    await database["cattle_documents"].insert_many(
        [
            {
                "_id": f"historico-{key}",
                "relational_property_id": str(fixture_ids[property_key]),
                "herd": {"herd_id": str(uuid4()), "herd_name": f"Rebanho Mongo {key}"},
                "lots": [],
                "report_created_at": now,
                "last_updated_at": now,
            }
            for key, property_key in (
                ("autorizado-a", "property_a"),
                ("autorizado-b", "property_b"),
                ("externo", "property_external"),
            )
        ]
    )
    medicine = {
        "report_created_at": now,
        "last_updated_at": now,
        "active_ingredient": "Ingrediente Teste",
        "indication": "Uso de integração",
        "therapeutic_category": {
            "therapeutic_category_id": str(fixture_ids["therapeutic_category_id"]),
            "name": "Antibiótico",
        },
        "supplier": {
            "supplier_id": str(fixture_ids["supplier_id"]),
            "company_name": "Fornecedor Teste",
        },
        "needs_prescription": False,
        "meat_withdrawal_days": 0,
        "dose": "1.0",
        "unit": "ml",
        "route": "INTRAMUSCULAR",
    }
    await database["medicine_documents"].insert_many(
        [
            {
                **medicine,
                "_id": document_id,
                "relational_medicine_id": str(medicine_id),
                "brand_name": name,
                "active": active,
            }
            for document_id, medicine_id, name, active in (
                ("medicine-active-a", fixture_ids["medicine_id"], "Medicamento Teste", True),
                ("medicine-active-b", uuid4(), "Outro Medicamento", True),
                ("medicine-inactive", uuid4(), "Medicamento Inativo", False),
            )
        ]
    )


async def _insert_synthetic_data(sql) -> dict[str, object]:
    """Cria P1/P3 autorizadas e P2 externa com chaves e rótulos repetidos."""

    user_id, owner_id = uuid4(), uuid4()
    property_a, property_b, property_external = uuid4(), uuid4(), uuid4()
    herd_a, herd_b, herd_external = uuid4(), uuid4(), uuid4()
    parent_a, exception_lot_a = uuid4(), uuid4()
    parent_b, exception_lot_b = uuid4(), uuid4()
    parent_external, exception_lot_external = uuid4(), uuid4()
    animal_a, animal_b, animal_external, animal_cross = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    collective_a, individual_a, collective_b, individual_b, external_application = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    occurrence_a, occurrence_b, occurrence_external, occurrence_cross = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    medicine_id, batch_id, supplier_id = uuid4(), uuid4(), uuid4()
    today = date.today()

    async with sql.transaction():
        await sql.execute("SELECT set_config('app.carga', 'true', true)")
        permission_id = await sql.fetchval("SELECT id FROM permission WHERE code = 'PECUARISTA'")
        user_firebase_uid = "firebase-usuario-mcp"
        await _insert_user(sql, user_id, permission_id, "usuario-mcp", user_firebase_uid)
        await _insert_user(sql, owner_id, permission_id, "proprietario-outro")
        await sql.executemany(
            """INSERT INTO property (id, user_id, name, city, state, active)
               VALUES ($1, $2, $3, 'Campo Grande', 'MS', TRUE)""",
            [
                (property_a, user_id, "Fazenda Repetida"),
                (property_b, owner_id, "Fazenda Compartilhada"),
                (property_external, owner_id, "Fazenda Repetida"),
            ],
        )
        await sql.execute(
            """INSERT INTO user_property (user_id, property_id, active, link_date)
               VALUES ($1, $2, TRUE, CURRENT_DATE)""",
            user_id,
            property_b,
        )
        await sql.executemany(
            """INSERT INTO herd (id, property_id, herd_name, breed, head_count, active)
               VALUES ($1, $2, 'Rebanho Mesmo Nome', 'Nelore', 0, TRUE)""",
            [
                (herd_a, property_a),
                (herd_b, property_b),
                (herd_external, property_external),
            ],
        )
        await sql.executemany(
            """INSERT INTO lot
               (id, herd_id, parent_lot_id, lot_code, category, is_exception_lot,
                exception_reason, head_count, open_date, active)
               VALUES ($1, $2, NULL, 'LOTE-REPETIDO', 'ENGORDA', FALSE, NULL, 20, $3, TRUE)""",
            [
                (parent_a, herd_a, today),
                (parent_b, herd_b, today),
                (parent_external, herd_external, today),
            ],
        )
        await sql.executemany(
            """INSERT INTO lot
               (id, herd_id, parent_lot_id, lot_code, category, is_exception_lot,
                exception_reason, head_count, open_date, active)
               VALUES ($1, $2, $3, 'EXCECAO', 'ENGORDA', TRUE, 'Teste integração', 1, $4, TRUE)""",
            [
                (exception_lot_a, herd_a, parent_a, today),
                (exception_lot_b, herd_b, parent_b, today),
                (exception_lot_external, herd_external, parent_external, today),
            ],
        )
        await sql.executemany(
            """INSERT INTO animal_exception
               (id, lot_id, origin_lot_id, ear_tag, category, birth_date, current_weight,
                exception_date, exception_reason, status)
               VALUES ($1, $2, $3, $4, 'BOI', $5, 400.0, $5, 'Teste integração', 'ATIVO')""",
            [
                (animal_a, exception_lot_a, parent_a, "BRINCO-IGUAL", today),
                (animal_b, exception_lot_b, parent_b, "BRINCO-IGUAL", today),
                (animal_external, exception_lot_external, parent_external, "BRINCO-IGUAL", today),
                (animal_cross, exception_lot_a, parent_external, "BRINCO-CRUZADO", today),
            ],
        )

        await sql.execute(
            """INSERT INTO lot
               (id, herd_id, lot_code, category, head_count, open_date, active)
               VALUES ($1, $2, 'LOTE-INATIVO', 'RECRIA', 0, $3, FALSE)""",
            uuid4(),
            herd_a,
            today,
        )

        await sql.execute(
            """INSERT INTO supplier (id, company_name, cnpj, active)
               VALUES ($1, 'Fornecedor Teste', $2, TRUE)""",
            supplier_id,
            f"{uuid4().int % 10**14:014d}",
        )
        category_id = await sql.fetchval(
            "SELECT id FROM therapeutic_category WHERE name = 'Antibiótico' LIMIT 1"
        )
        await sql.execute(
            """INSERT INTO medicine
               (id, supplier_id, therapeutic_category_id, brand_name, active_ingredient,
                indication, meat_withdrawal_days, dose, unit, route, active)
               VALUES ($1, $2, $3, 'Medicamento Teste', 'Ingrediente Teste',
                       'Uso de integração', 0, 1.0, 'ml', 'INTRAMUSCULAR', TRUE)""",
            medicine_id,
            supplier_id,
            category_id,
        )
        await sql.execute(
            """INSERT INTO medicine_batch
               (id, medicine_id, batch_number, manufacture_date, expiry_date, delivery_date,
                initial_quantity, status)
               VALUES ($1, $2, 'BATCH-TESTE', $3, $4, $3, 1000, 'DISPONIVEL')""",
            batch_id,
            medicine_id,
            today,
            date(today.year + 1, today.month, min(today.day, 28)),
        )
        application_rows = [
            (collective_a, parent_a, user_id),
            (individual_a, parent_a, user_id),
            (collective_b, parent_b, user_id),
            (individual_b, parent_b, user_id),
            (external_application, parent_external, owner_id),
        ]
        await sql.executemany(
            """INSERT INTO application
               (id, lot_id, medicine_batch_id, user_id, application_date, dose, unit,
                route, head_count_applied, active)
               VALUES ($1, $2, $3, $4, $5, 1.0, 'ml', 'INTRAMUSCULAR', 1, TRUE)""",
            [
                (application_id, lot_id, batch_id, app_user_id, today)
                for application_id, lot_id, app_user_id in application_rows
            ],
        )
        await sql.executemany(
            """INSERT INTO application_animal
               (id, application_id, animal_exception_id, dose_given, weight_at_time,
                meat_withdrawal_end, released_for_slaughter)
               VALUES ($1, $2, $3, 1.0, 400, $4, TRUE)""",
            [
                (uuid4(), individual_a, animal_a, today),
                (uuid4(), individual_b, animal_b, today),
                (uuid4(), external_application, animal_external, today),
            ],
        )
        await sql.executemany(
            """INSERT INTO occurrence
               (id, animal_exception_id, user_id, type, occurrence_date,
                description, diagnosis, resolved, resolved_at)
               VALUES ($1, $2, $3, 'DOENCA', $4, 'Ocorrência de teste',
                       'Diagnóstico teste', TRUE, $4)""",
            [
                (occurrence_a, animal_a, user_id, today),
                (occurrence_b, animal_b, user_id, today),
                (occurrence_external, animal_external, owner_id, today),
                (occurrence_cross, animal_cross, user_id, today),
            ],
        )
        await sql.executemany(
            """INSERT INTO animal_lot_history
               (id, animal_exception_id, lot_id, entry_date, exit_date, reason)
               VALUES ($1, $2, $3, $4, $5, 'TESTE')""",
            [
                # Model a valid move: the animal left its authorized lot before
                # entering the external property. The schema permits only one
                # active (exit_date IS NULL) history row per animal.
                (uuid4(), animal_a, parent_a, today, today),
                (uuid4(), animal_a, parent_external, today, None),
                (uuid4(), animal_b, parent_b, today, None),
                (uuid4(), animal_external, parent_external, today, None),
            ],
        )

    return {
        "user_id": user_id,
        "firebase_uid": user_firebase_uid,
        "medicine_id": medicine_id,
        "supplier_id": supplier_id,
        "therapeutic_category_id": category_id,
        "property_a": property_a,
        "property_b": property_b,
        "property_external": property_external,
        "property_ids_authorized": {property_a, property_b},
        "herd_a": herd_a,
        "herd_external": herd_external,
        "exception_lot_a": exception_lot_a,
        "exception_lot_external": exception_lot_external,
        "animal_a": animal_a,
        "animal_b": animal_b,
        "animal_external": animal_external,
        "individual_a": individual_a,
        "individual_b": individual_b,
        "today": today,
    }


async def _insert_user(
    sql, user_id, permission_id, suffix: str, firebase_uid: str | None = None
) -> None:
    firebase_uid = firebase_uid or f"firebase-{suffix}"
    await sql.execute(
        """INSERT INTO app_user (id, permission_id, name, cpf, email, active, firebase_uid)
           VALUES ($1, $2, $3, $4, $5, TRUE, $6)""",
        user_id,
        permission_id,
        suffix,
        f"{uuid4().int % 10**11:011d}",
        f"{uuid4().hex}@example.test",
        firebase_uid,
    )
