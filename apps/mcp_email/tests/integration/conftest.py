"""Infraestrutura real de teste; somente bancos descartáveis são utilizados."""

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[4]
SQL_ADMIN_DSN = "postgresql://email_test:email_test_local_only@127.0.0.1:54328/email_test"
MONGO_URI = "mongodb://127.0.0.1:27028"
MAILPIT_URL = "http://127.0.0.1:8028"


class McpHarness:
    def __init__(self, tmp_path, database_name, mongo, private_key):
        self.database_name = database_name
        self.database = mongo[database_name]
        self.private_key = private_key
        self.requester = uuid4()
        self.token = uuid4().hex
        self.recipient_name = "Matheus Fazan"
        self.recipient_email = "matheus@example.test"
        self.title = f"Relatório integração {uuid4().hex}"
        self.log_path = tmp_path / "mcp-server.log"
        self.process = None
        self.log_file = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"

    async def start(self):
        import httpx

        env = dict(os.environ)
        env.update(
            PYTHONPATH=os.pathsep.join((str(ROOT / "apps/mcp_email/src"), str(ROOT))),
            PYTHONUNBUFFERED="1",
            MCP_EMAIL_MONGO_URI=MONGO_URI,
            MCP_EMAIL_MONGO_DATABASE=self.database_name,
            MCP_EMAIL_SQL_DSN=(
                "postgresql://email_test:email_test_local_only@127.0.0.1:54328/"
                + self.database_name
            ),
            MCP_EMAIL_TRUSTED_SIGNATURE_KEYS=json.dumps(
                {
                    "test-key": base64.b64encode(
                        self.private_key.public_key().public_bytes_raw()
                    ).decode()
                }
            ),
            MCP_EMAIL_INTERNAL_SERVICE_TOKEN=self.token,
            MCP_EMAIL_AUTH_ISSUER_URL=f"http://127.0.0.1:{self.port}/test-issuer",
            MCP_EMAIL_RESOURCE_SERVER_URL=self.url,
            MCP_EMAIL_HOST="127.0.0.1",
            MCP_EMAIL_PORT=str(self.port),
            MCP_EMAIL_NO_REPLY_ADDRESS="no-reply@argos.example.test",
            MCP_EMAIL_SMTP_HOST="127.0.0.1",
            MCP_EMAIL_SMTP_PORT="10258",
            MCP_EMAIL_SMTP_USE_TLS="false",
            MCP_EMAIL_SMTP_USERNAME="",
            MCP_EMAIL_SMTP_PASSWORD="",
        )
        self.log_file = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "argos_mcp_email"],
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
        raise RuntimeError("MCP não iniciou em 30s: " + self.log_path.read_text(encoding="utf-8"))

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
    async def client(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=httpx.Timeout(30, read=300),
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as streams:
                read, write, _ = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def messages(self):
        import httpx

        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"{MAILPIT_URL}/api/v1/messages")
            response.raise_for_status()
            return [
                message
                for message in response.json()["messages"]
                if self.title in message["Subject"]
            ]


@pytest_asyncio.fixture
async def mcp_harness(tmp_path):
    if os.getenv("MCP_EMAIL_RUN_INTEGRATION") != "1":
        pytest.skip("suba o compose de integração e defina MCP_EMAIL_RUN_INTEGRATION=1")

    import asyncpg
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from motor.motor_asyncio import AsyncIOMotorClient

    database_name = "email_integration_" + uuid4().hex
    mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    harness = McpHarness(tmp_path, database_name, mongo, Ed25519PrivateKey.generate())
    admin = await asyncpg.connect(SQL_ADMIN_DSN, timeout=5)
    await admin.execute(f'CREATE DATABASE "{database_name}"')
    try:
        await mongo.admin.command("ping")
        sql = await asyncpg.connect(
            "postgresql://email_test:email_test_local_only@127.0.0.1:54328/" + database_name
        )
        try:
            await sql.execute(
                "CREATE TABLE app_user (id UUID PRIMARY KEY, name TEXT, email TEXT, active BOOLEAN)"
            )
            await sql.execute(
                "INSERT INTO app_user VALUES ($1, $2, $3, TRUE)",
                uuid4(),
                harness.recipient_name,
                harness.recipient_email,
            )
        finally:
            await sql.close()
        await harness.start()
        yield harness
    finally:
        await harness.stop()
        await mongo.drop_database(database_name)
        mongo.close()
        await admin.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        await admin.close()
