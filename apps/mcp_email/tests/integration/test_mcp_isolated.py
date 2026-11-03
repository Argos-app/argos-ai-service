"""Três tools pelo SDK real, Mongo/PostgreSQL reais e SMTP capturado pelo Mailpit."""

import asyncio
import base64
import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from packages.contracts import LotReportData, LotReportEnvelope, LotReportIdentity

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def preparation_payload(harness):
    now = datetime.now(UTC)
    property_id = uuid4()
    envelope = LotReportEnvelope(
        report=LotReportData(
            requested_lot=LotReportIdentity(
                lot_id=uuid4(),
                lot_code="BOI-FELIZ",
                lot_category="ENGORDA",
                lot_active=True,
                lot_is_exception=False,
                lot_head_count=20,
                lot_open_date=date(2026, 1, 1),
                fit_for_slaughter=True,
                herd_id=uuid4(),
                herd_name="Rebanho Feliz",
                herd_breed="Nelore",
                property_id=property_id,
                property_name="Fazenda de teste",
            ),
            queried_at=now,
        ),
        principal_user_id=harness.requester,
        authorized_property_ids=(property_id,),
        valid_until=now + timedelta(minutes=5),
        signature_key_id="test-key",
        signature="placeholder",
        payload_hash="placeholder",
    )
    canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode()
    envelope = envelope.model_copy(
        update={
            "payload_hash": "sha256:" + sha256(canonical).hexdigest(),
            "signature": base64.b64encode(harness.private_key.sign(canonical)).decode(),
        }
    )
    return {
        "report_source": envelope.model_dump(mode="json"),
        "requested_by": str(harness.requester),
        "report_title": harness.title,
        "recipients": [{"kind": "name", "value": harness.recipient_name}],
    }


def result_json(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


async def test_real_tools_persist_send_once_and_recover_after_restart(mcp_harness):
    import httpx

    harness = mcp_harness
    async with harness.client() as session:
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "prepare_report",
            "send_report",
            "get_report_status",
        }
        for tool in tools:
            assert set(tool.inputSchema["properties"]) == {"payload"}

        prepared = result_json(
            await session.call_tool("prepare_report", {"payload": preparation_payload(harness)})
        )
        assert prepared["status"] == "prepared"
        report = await harness.database.ai_email_prepared_reports.find_one(
            {"_id": prepared["report_id"]}
        )
        assert report["body"] == prepared["body"]
        assert report["recipients"][0]["email"] == harness.recipient_email

        indexes = await harness.database.ai_email_dispatches.index_information()
        assert indexes["uq_dispatch_idempotency_key"]["unique"] is True
        send_payload = {
            "report_id": prepared["report_id"],
            "requested_by": str(harness.requester),
            "idempotency_key": "send-" + uuid4().hex,
        }
        results = await asyncio.gather(
            *(session.call_tool("send_report", {"payload": send_payload}) for _ in range(6))
        )
        sends = [result_json(result) for result in results]
        assert len({result["operation_id"] for result in sends}) == 1
        assert {result["status"] for result in sends} <= {"pending", "accepted"}
        operation_id = sends[0]["operation_id"]
        status_payload = {"operation_id": operation_id, "requested_by": str(harness.requester)}
        status = result_json(
            await session.call_tool("get_report_status", {"payload": status_payload})
        )
        assert status["status"] == "accepted"
        operation = await harness.database.ai_email_dispatches.find_one({"_id": operation_id})
        assert operation["state"] == "accepted"
        assert operation["send_claimed"] is True

    messages = await harness.messages()
    assert len(messages) == 1
    async with httpx.AsyncClient(trust_env=False) as client:
        captured = await client.get("http://127.0.0.1:8028/api/v1/message/" + messages[0]["ID"])
        captured.raise_for_status()
        email = captured.json()
        assert email["From"]["Address"] == "no-reply@argos.example.test"
        assert email["To"][0]["Address"] == harness.recipient_email
        assert email["Subject"] == prepared["subject"]
        assert email["Text"].replace("\r\n", "\n").strip() == prepared["body"]

    await harness.stop()
    await harness.start()
    async with harness.client() as session:
        restored = result_json(
            await session.call_tool("get_report_status", {"payload": status_payload})
        )
        repeated = result_json(await session.call_tool("send_report", {"payload": send_payload}))
        assert restored["status"] == repeated["status"] == "accepted"
        assert restored["operation_id"] == repeated["operation_id"] == operation_id
    assert len(await harness.messages()) == 1
    assert await harness.database.ai_email_dispatches.count_documents({}) == 1


async def test_transport_auth_and_altered_signature_are_rejected(mcp_harness):
    import httpx

    harness = mcp_harness
    async with httpx.AsyncClient(trust_env=False) as client:
        for headers in ({}, {"Authorization": "Bearer wrong-token"}):
            response = await client.post(
                harness.url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert response.status_code == 401

    payload = preparation_payload(harness)
    payload["report_source"]["report"]["requested_lot"]["lot_head_count"] = 999
    async with harness.client() as session:
        result = await session.call_tool("prepare_report", {"payload": payload})
        assert result.isError
    assert await harness.database.ai_email_prepared_reports.count_documents({}) == 0
    assert await harness.database.ai_email_dispatches.count_documents({}) == 0
    assert await harness.messages() == []
