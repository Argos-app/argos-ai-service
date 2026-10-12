"""Tool de predição via servidor MCP real e PostgreSQL descartável."""

import json
from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _response(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


async def test_real_tool_calculates_annual_history_with_unknown_coverage(prediction_harness):
    import asyncpg

    harness = prediction_harness
    as_of = harness.as_of
    async with harness.client() as session:
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {"estimate_medicine_consumption"}
        assert set(tools[0].inputSchema["properties"]) == {"payload"}

        result = _response(
            await session.call_tool(
                "estimate_medicine_consumption",
                {
                    "payload": {
                        "property_ids": [str(harness.property_a)],
                        "medicine_names": ["ivermectina"],
                        "as_of": as_of.isoformat(),
                        "horizon_days": 1,
                    }
                },
            )
        )

    assert result["status"] == "estimated"
    assert result["mode"] == "simulation"
    assert result["data_origin"] == "synthetic"
    assert result["property_ids"] == [str(harness.property_a)]
    assert result["coverage"][0]["status"] == "unknown"
    assert any("completude do histórico" in item for item in result["limitations"])
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["property_id"] == str(harness.property_a)
    assert item["medicine_name"] == "Marca de teste"
    assert item["active_ingredient"] == "Ivermectina"
    assert item["historical_quantity"] == "120.0"
    assert item["historical_application_events"] == 12
    assert item["historical_head_administrations"] == 48
    month = next(
        entry
        for entry in item["monthly_history"]
        if entry["month"] == (harness.as_of + timedelta(days=1)).month
    )
    with localcontext(Context(prec=50)):
        assert Decimal(item["estimated_quantity"]) == Decimal(10) / month["calendar_days"]

    admin = await asyncpg.connect(harness.database_dsn)
    try:
        assert await admin.fetchval("SELECT COUNT(*) FROM application") == 36
    finally:
        await admin.close()


async def test_authentication_and_property_filter_are_enforced(prediction_harness):
    import httpx

    harness = prediction_harness
    valid_delegation = harness.delegation_token()
    async with httpx.AsyncClient(trust_env=False) as client:
        missing = await client.post(
            harness.url,
            headers={"Authorization": f"Bearer {harness.service_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert missing.status_code == 401
        invalid = await client.post(
            harness.url,
            headers={
                "Authorization": "Bearer invalid-token",
                "X-Argos-Delegation": valid_delegation,
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert invalid.status_code == 401

    async with harness.client() as session:
        denied = await session.call_tool(
            "estimate_medicine_consumption",
            {
                "payload": {
                    "property_ids": [str(harness.property_b)],
                    "medicine_names": ["Ivermectina"],
                    "as_of": harness.as_of.isoformat(),
                    "horizon_days": 1,
                }
            },
        )
    assert denied.isError


async def test_same_medicine_is_aggregated_only_for_each_delegated_property(prediction_harness):
    harness = prediction_harness
    results = {}
    for property_id in (harness.property_a, harness.property_b):
        delegation = harness.delegation_token(authorized_properties=(property_id,))
        async with harness.client(delegation=delegation) as session:
            result = _response(
                await session.call_tool(
                    "estimate_medicine_consumption",
                    {
                        "payload": {
                            "property_ids": [str(property_id)],
                            "medicine_names": ["Marca de teste"],
                            "as_of": harness.as_of.isoformat(),
                            "horizon_days": 1,
                        }
                    },
                )
            )
        assert result["property_ids"] == [str(property_id)]
        assert len(result["items"]) == 1
        assert result["items"][0]["property_id"] == str(property_id)
        results[property_id] = Decimal(result["items"][0]["historical_quantity"])

    # The same medicine has distinct consumption in A and B; results stay scoped.
    assert results[harness.property_a] == Decimal("120.0")
    assert results[harness.property_b] == Decimal("600.0")


async def test_runtime_database_role_is_select_only(prediction_harness):
    import asyncpg

    harness = prediction_harness
    connection = await asyncpg.connect(harness.reader_dsn)
    try:
        assert await connection.fetchval(
            "SELECT has_table_privilege(current_user, 'application', 'SELECT')"
        )
        assert not await connection.fetchval(
            "SELECT has_table_privilege(current_user, 'application', 'INSERT')"
        )
        statements = (
            "INSERT INTO application (id) VALUES (gen_random_uuid())",
            "UPDATE application SET active = FALSE",
            "DELETE FROM application",
            "TRUNCATE application",
            "CREATE TABLE forbidden_write (id UUID)",
        )
        for statement in statements:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)
    finally:
        await connection.close()
