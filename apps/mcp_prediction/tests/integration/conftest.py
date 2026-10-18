"""Banco PostgreSQL descartável e processo MCP real para integração."""

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[4]
SQL_ADMIN_DSN = os.getenv("MCP_PREDICTION_TEST_ADMIN_DSN") or (
    "postgresql://mcp_prediction_admin:mcp_prediction_local_only@127.0.0.1:54330/"
    "mcp_prediction_test"
)

_SCHEMA = """
CREATE TABLE permission (id UUID PRIMARY KEY, code TEXT NOT NULL, active BOOLEAN NOT NULL);
CREATE TABLE app_user (
    id UUID PRIMARY KEY, permission_id UUID NOT NULL, name TEXT NOT NULL,
    cpf TEXT NOT NULL, email TEXT NOT NULL, active BOOLEAN NOT NULL,
    blocked_until TIMESTAMPTZ
);
CREATE TABLE property (id UUID PRIMARY KEY, user_id UUID NOT NULL, name TEXT NOT NULL,
                       active BOOLEAN NOT NULL);
CREATE TABLE user_property (
    user_id UUID NOT NULL, property_id UUID NOT NULL, active BOOLEAN NOT NULL,
    link_date DATE NOT NULL, unlink_date DATE
);
CREATE TABLE herd (id UUID PRIMARY KEY, property_id UUID NOT NULL);
CREATE TABLE lot (id UUID PRIMARY KEY, herd_id UUID NOT NULL);
CREATE TABLE medicine (
    id UUID PRIMARY KEY, brand_name VARCHAR(80) NOT NULL,
    active_ingredient VARCHAR(80) NOT NULL
);
CREATE TABLE medicine_batch (id UUID PRIMARY KEY, medicine_id UUID NOT NULL);
CREATE TABLE application (
    id UUID PRIMARY KEY, lot_id UUID NOT NULL, medicine_batch_id UUID NOT NULL,
    application_date DATE NOT NULL, dose NUMERIC NOT NULL, unit TEXT NOT NULL,
    head_count_applied INTEGER NOT NULL, active BOOLEAN NOT NULL
);
CREATE TABLE application_animal (
    id UUID PRIMARY KEY, application_id UUID NOT NULL, dose_given NUMERIC NOT NULL
);
"""


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _history_dates(as_of: date) -> tuple[date, ...]:
    try:
        anniversary = as_of.replace(year=as_of.year - 1)
    except ValueError:
        anniversary = date(as_of.year - 1, 2, 28)
    start = anniversary + timedelta(days=1)
    first_each_month = {}
    for offset in range((as_of - start).days + 1):
        current = start + timedelta(days=offset)
        first_each_month.setdefault(current.month, current)
    if len(first_each_month) != 12:
        raise AssertionError("a janela anual deve conter todos os meses")
    return tuple(first_each_month[month] for month in range(1, 13))


class PredictionHarness:
    def __init__(self, tmp_path, database_name, reader_role, reader_password, private_key):
        self.database_name = database_name
        self.reader_role = reader_role
        self.reader_password = reader_password
        self.private_key = private_key
        self.as_of = date.today() - timedelta(days=1)
        self.user_id = uuid4()
        self.property_a = uuid4()
        self.property_b = uuid4()
        self.property_external = uuid4()
        self.medicine_id = uuid4()
        self.service_token = uuid4().hex
        self.log_path = tmp_path / "mcp-prediction.log"
        self.process = None
        self.log_file = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.database_dsn = SQL_ADMIN_DSN.rsplit("/", 1)[0] + "/" + database_name
        database_endpoint = urlsplit(SQL_ADMIN_DSN)
        self.reader_dsn = (
            f"postgresql://{reader_role}:{reader_password}@{database_endpoint.hostname}:"
            f"{database_endpoint.port or 5432}/{database_name}"
        )

    def delegation_token(self, *, authorized_properties=None, overrides=None):
        now = int(datetime.now(UTC).timestamp())
        claims = {
            "v": 1,
            "iss": "argos-main-service-test",
            "aud": self.url,
            "sub": str(self.user_id),
            "authorized_property_ids": [
                str(value) for value in (authorized_properties or (self.property_a,))
            ],
            "agent": "analyst",
            "operations": ["prediction.read"],
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

    async def start(self):
        import httpx
        from cryptography.hazmat.primitives import serialization

        public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        environment = dict(os.environ)
        environment.update(
            PYTHONPATH=os.pathsep.join((str(ROOT / "apps/mcp_prediction/src"), str(ROOT))),
            PYTHONUNBUFFERED="1",
            MCP_PREDICTION_SQL_DSN=self.reader_dsn,
            MCP_PREDICTION_INTERNAL_SERVICE_TOKEN=self.service_token,
            MCP_PREDICTION_AUTH_ISSUER_URL=f"http://127.0.0.1:{self.port}/test-issuer",
            MCP_PREDICTION_RESOURCE_SERVER_URL=self.url,
            MCP_PREDICTION_DELEGATION_ISSUER="argos-main-service-test",
            MCP_PREDICTION_TRUSTED_DELEGATION_KEYS=json.dumps(
                {"integration-key": base64.b64encode(public_key).decode("ascii")}
            ),
            MCP_PREDICTION_DATA_ORIGIN="synthetic",
            MCP_PREDICTION_HOST="127.0.0.1",
            MCP_PREDICTION_PORT=str(self.port),
        )
        self.log_file = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "argos_mcp_prediction"],
            cwd=ROOT,
            env=environment,
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
        raise RuntimeError("MCP de predição não iniciou em 30s: " + self.log_path.read_text())

    async def stop(self):
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

    @asynccontextmanager
    async def client(self, *, token=None, delegation=None):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {
            "Authorization": f"Bearer {token or self.service_token}",
            "X-Argos-Delegation": delegation or self.delegation_token(),
        }
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30, read=120),
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as streams:
                read, write, _ = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


@pytest_asyncio.fixture
async def prediction_harness(tmp_path):
    if os.getenv("MCP_PREDICTION_RUN_INTEGRATION") != "1":
        pytest.skip("suba o compose de integração e defina MCP_PREDICTION_RUN_INTEGRATION=1")

    import asyncpg
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    database_name = "prediction_it_" + uuid4().hex
    reader_role = "pred_reader_" + uuid4().hex[:12]
    reader_password = uuid4().hex
    as_of = date.today() - timedelta(days=1)
    admin = await asyncpg.connect(SQL_ADMIN_DSN, timeout=5)
    harness = PredictionHarness(
        tmp_path,
        database_name,
        reader_role,
        reader_password,
        Ed25519PrivateKey.generate(),
    )
    harness.as_of = as_of
    database_created = False
    role_created = False
    try:
        await admin.execute(f"CREATE ROLE \"{reader_role}\" LOGIN PASSWORD '{reader_password}'")
        role_created = True
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        database_created = True
        await admin.execute(f'GRANT CONNECT ON DATABASE "{database_name}" TO "{reader_role}"')
        sql = await asyncpg.connect(harness.database_dsn, timeout=10)
        try:
            await sql.execute(_SCHEMA)
            await _seed_database(sql, harness, as_of)
            await sql.execute(f'GRANT USAGE ON SCHEMA public TO "{reader_role}"')
            await sql.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{reader_role}"')
        finally:
            await sql.close()
        await harness.start()
        yield harness
    finally:
        await harness.stop()
        if database_created:
            await admin.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        if role_created:
            await admin.execute(f'DROP ROLE "{reader_role}"')
        await admin.close()


async def _seed_database(sql, harness, as_of):
    permission_id = uuid4()
    other_user_id = uuid4()
    herd_ids = {
        property_id: uuid4()
        for property_id in (
            harness.property_a,
            harness.property_b,
            harness.property_external,
        )
    }
    lot_ids = {property_id: uuid4() for property_id in herd_ids}
    batch_id = uuid4()
    await sql.execute("INSERT INTO permission VALUES ($1, 'ANALISTA', TRUE)", permission_id)
    await sql.executemany(
        """INSERT INTO app_user (id, permission_id, name, cpf, email, active, blocked_until)
           VALUES ($1, $2, $3, $4, $5, TRUE, NULL)""",
        [
            (
                harness.user_id,
                permission_id,
                "Analista de integração",
                uuid4().hex,
                "analyst@test",
            ),
            (other_user_id, permission_id, "Outro usuário", uuid4().hex, "outro@test"),
        ],
    )
    await sql.executemany(
        "INSERT INTO property VALUES ($1, $2, $3, TRUE)",
        [
            (harness.property_a, harness.user_id, "Propriedade autorizada"),
            (harness.property_b, other_user_id, "Propriedade não delegada"),
            (harness.property_external, other_user_id, "Propriedade externa"),
        ],
    )
    # A conta possui acesso cadastral a B, mas a delegação assina somente A.
    await sql.execute(
        "INSERT INTO user_property VALUES ($1, $2, TRUE, CURRENT_DATE, NULL)",
        harness.user_id,
        harness.property_b,
    )
    await sql.executemany(
        "INSERT INTO herd VALUES ($1, $2)",
        [(herd_id, property_id) for property_id, herd_id in herd_ids.items()],
    )
    await sql.executemany(
        "INSERT INTO lot VALUES ($1, $2)",
        [(lot_id, herd_ids[property_id]) for property_id, lot_id in lot_ids.items()],
    )
    await sql.execute(
        "INSERT INTO medicine VALUES ($1, 'Marca de teste', 'Ivermectina')",
        harness.medicine_id,
    )
    await sql.execute("INSERT INTO medicine_batch VALUES ($1, $2)", batch_id, harness.medicine_id)
    application_rows = []
    for property_id, heads in (
        (harness.property_a, 4),
        (harness.property_b, 20),
        (harness.property_external, 30),
    ):
        for application_date in _history_dates(as_of):
            application_rows.append(
                (uuid4(), lot_ids[property_id], batch_id, application_date, heads)
            )
    await sql.executemany(
        """INSERT INTO application
           (id, lot_id, medicine_batch_id, application_date, dose, unit, head_count_applied, active)
           VALUES ($1, $2, $3, $4, 2.5, 'ml', $5, TRUE)""",
        application_rows,
    )
