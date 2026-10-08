"""Valida ferramentas SQL e Mongo contra dados sintéticos descartáveis."""

import json
import os
import time
from decimal import Decimal

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        os.getenv("MCP_DATA_RUN_INTEGRATION") != "1",
        reason="suba o compose de integração e defina MCP_DATA_RUN_INTEGRATION=1",
    ),
]


def _result_json(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


async def test_tools_and_property_herd_lot_queries_are_scoped_and_paginated(mcp_data_harness):
    harness = mcp_data_harness
    async with harness.client() as session:
        tools = (await session.list_tools()).tools
        expected_tools = {
            "list_properties",
            "list_herds",
            "list_lots",
            "list_exception_animals",
            "list_applications",
            "list_application_animals",
            "list_occurrences",
            "list_animal_lot_history",
            "list_cattle_documents",
            "get_farm_summary",
            "summarize_exception_animals",
            "list_medicines",
            "list_medicine_documents",
        }
        assert {tool.name for tool in tools} == expected_tools
        for tool in tools:
            assert set(tool.inputSchema["properties"]) == {"payload"}

        first = _result_json(
            await session.call_tool("list_properties", {"payload": {"page_size": 1}})
        )
        assert len(first["items"]) == 1
        assert first["has_more"] is True
        assert first["next_offset"] == 1

        second = _result_json(
            await session.call_tool("list_properties", {"payload": {"page_size": 1, "offset": 1}})
        )
        property_ids = {item["property_id"] for item in first["items"] + second["items"]}
        assert property_ids == {
            str(harness.fixture_ids["property_a"]),
            str(harness.fixture_ids["property_b"]),
        }
        assert second["has_more"] is False

        external_selection = await session.call_tool(
            "list_properties",
            {"payload": {"property_ids": [str(harness.fixture_ids["property_external"])]}},
        )
        assert external_selection.isError

        herds = _result_json(await session.call_tool("list_herds", {"payload": {"page_size": 10}}))
        assert {item["property_id"] for item in herds["items"]} == property_ids
        assert all(item["herd_name"] == "Rebanho Mesmo Nome" for item in herds["items"])

        lots = _result_json(
            await session.call_tool(
                "list_lots",
                {
                    "payload": {
                        "lot_code": "LOTE-REPETIDO",
                        "date_from": str(harness.fixture_ids["today"]),
                        "date_to": str(harness.fixture_ids["today"]),
                        "page_size": 10,
                    }
                },
            )
        )
        assert len(lots["items"]) == 2
        assert {item["property_id"] for item in lots["items"]} == property_ids

        summary = _result_json(
            await session.call_tool(
                "get_farm_summary",
                {"payload": {"group_by": "property", "page_size": 10}},
            )
        )
        summary_by_property = {item["dimensions"]["property"]: item for item in summary["items"]}
        assert set(summary_by_property) == property_ids
        assert all(item["herd_count"] == 1 for item in summary["items"])
        assert all(item["lot_count"] == 2 for item in summary["items"])
        assert all(item["head_count"] == 21 for item in summary["items"])

        unauthorized_summary = await session.call_tool(
            "get_farm_summary",
            {
                "payload": {
                    "property_ids": [str(harness.fixture_ids["property_external"])],
                }
            },
        )
        assert unauthorized_summary.isError

        exception_summary = _result_json(
            await session.call_tool(
                "summarize_exception_animals",
                {"payload": {"group_by": "status", "page_size": 10}},
            )
        )
        active = next(
            item for item in exception_summary["items"] if item["dimensions"]["status"] == "ATIVO"
        )
        assert active["animal_count"] == 2
        assert active["known_weight_count"] == 2
        assert active["missing_weight_count"] == 0
        assert Decimal(active["known_weight_sum"]) == Decimal("800")

        unauthorized_exceptions = await session.call_tool(
            "summarize_exception_animals",
            {
                "payload": {
                    "property_ids": [str(harness.fixture_ids["property_external"])],
                }
            },
        )
        assert unauthorized_exceptions.isError


async def test_farm_summary_groups_and_quantity_filters_match_scoped_lot_details(mcp_data_harness):
    harness = mcp_data_harness
    async with harness.client() as session:
        details = _result_json(await session.call_tool("list_lots", {"payload": {"active": True}}))
        lots = details["items"]
        dimensions = {
            "property": "property_id",
            "herd": "herd_id",
            "lot": "lot_id",
            "lot_category": "category",
            "exception_lot": "is_exception_lot",
            "active": "active",
        }
        for grouping, field in dimensions.items():
            result = _result_json(
                await session.call_tool("get_farm_summary", {"payload": {"group_by": grouping}})
            )
            expected = {}
            for lot in lots:
                expected.setdefault(lot[field], []).append(lot)
            assert {row["dimensions"][grouping] for row in result["items"]} == set(expected)
            for row in result["items"]:
                grouped = expected[row["dimensions"][grouping]]
                assert row["head_count"] == sum(lot["head_count"] for lot in grouped)
                assert row["lot_count"] == len(grouped)
                assert row["herd_count"] == len({lot["herd_id"] for lot in grouped})

        quantity_filters = {"head_count_min": 20, "head_count_max": 20}
        filtered_details = _result_json(
            await session.call_tool("list_lots", {"payload": {**quantity_filters, "active": True}})
        )["items"]
        assert len(filtered_details) == 2
        all_groups = _result_json(
            await session.call_tool(
                "get_farm_summary", {"payload": {**quantity_filters, "group_by": "lot"}}
            )
        )
        assert {row["dimensions"]["lot"] for row in all_groups["items"]} == {
            lot["lot_id"] for lot in filtered_details
        }
        assert sum(row["head_count"] for row in all_groups["items"]) == 40
        assert {row["dimensions"]["label"] for row in all_groups["items"]} == {"LOTE-REPETIDO"}

        first = _result_json(
            await session.call_tool(
                "get_farm_summary",
                {"payload": {**quantity_filters, "group_by": "lot", "page_size": 1}},
            )
        )
        second = _result_json(
            await session.call_tool(
                "get_farm_summary",
                {
                    "payload": {
                        **quantity_filters,
                        "group_by": "lot",
                        "page_size": 1,
                        "offset": first["next_offset"],
                    }
                },
            )
        )
        assert first["has_more"] is True
        assert second["has_more"] is False
        assert first["items"] + second["items"] == all_groups["items"]

        activity = _result_json(
            await session.call_tool(
                "get_farm_summary", {"payload": {"active": None, "group_by": "active"}}
            )
        )
        by_activity = {row["dimensions"]["active"]: row for row in activity["items"]}
        assert set(by_activity) == {True, False}
        assert by_activity[True]["head_count"] == 42
        assert by_activity[True]["lot_count"] == 4
        assert by_activity[False]["head_count"] == 0
        assert by_activity[False]["lot_count"] == 1
        zero = _result_json(
            await session.call_tool(
                "get_farm_summary",
                {"payload": {"active": False, "head_count_min": 0, "head_count_max": 0}},
            )
        )
        assert len(zero["items"]) == 1
        assert zero["items"][0]["head_count"] == 0


async def test_exception_summary_filters_are_valid_and_preserve_property_scope(mcp_data_harness):
    harness = mcp_data_harness
    ids = harness.fixture_ids
    async with harness.client() as session:
        for payload in (
            {"herd_id": str(ids["herd_a"])},
            {"lot_id": str(ids["exception_lot_a"])},
            {
                "property_ids": [str(ids["property_a"])],
                "herd_id": str(ids["herd_a"]),
                "lot_id": str(ids["exception_lot_a"]),
                "category": "BOI",
                "statuses": ["ATIVO"],
                "ear_tag": "brinco-igual",
                "reason": "integração",
                "date_from": str(ids["today"]),
                "date_to": str(ids["today"]),
            },
        ):
            result = _result_json(
                await session.call_tool("summarize_exception_animals", {"payload": payload})
            )
            assert len(result["items"]) == 1
            assert result["items"][0]["animal_count"] == 1
            assert Decimal(result["items"][0]["known_weight_sum"]) == Decimal("400")

        category = _result_json(
            await session.call_tool("summarize_exception_animals", {"payload": {"category": "BOI"}})
        )
        assert category["items"][0]["animal_count"] == 2
        for payload in (
            {"herd_id": str(ids["herd_external"])},
            {"lot_id": str(ids["exception_lot_external"])},
            {"ear_tag": "BRINCO-CRUZADO"},
            {"ear_tag": "' OR 1=1 --"},
            {"category": "BEZERRO"},
        ):
            result = _result_json(
                await session.call_tool("summarize_exception_animals", {"payload": payload})
            )
            assert result["items"] == []

        filtered_animals = _result_json(
            await session.call_tool(
                "list_exception_animals", {"payload": {"category": "BOI", "ear_tag": "igual"}}
            )
        )["items"]
        for grouping, field in {
            "property": "property_id",
            "lot": "current_lot_id",
            "category": "category",
            "status": "status",
            "reason": "exception_reason",
        }.items():
            result = _result_json(
                await session.call_tool(
                    "summarize_exception_animals",
                    {"payload": {"group_by": grouping, "category": "BOI", "ear_tag": "igual"}},
                )
            )
            expected = {}
            for animal in filtered_animals:
                expected.setdefault(animal[field], []).append(animal)
            assert {row["dimensions"][grouping] for row in result["items"]} == set(expected)
            for row in result["items"]:
                grouped = expected[row["dimensions"][grouping]]
                assert row["animal_count"] == len(grouped)
                assert Decimal(row["known_weight_sum"]) == sum(
                    Decimal(animal["current_weight"]) for animal in grouped
                )


async def test_cattle_documents_are_paginated_and_scoped_by_authorized_properties(
    mcp_data_harness,
):
    harness = mcp_data_harness
    async with harness.client() as session:
        page = _result_json(
            await session.call_tool("list_cattle_documents", {"payload": {"page_size": 1}})
        )
        assert len(page["items"]) == 1
        assert page["has_more"] is True
        assert page["next_offset"] == 1
        assert page["items"][0]["relational_property_id"] in {
            str(harness.fixture_ids["property_a"]),
            str(harness.fixture_ids["property_b"]),
        }

        all_authorized = _result_json(
            await session.call_tool("list_cattle_documents", {"payload": {"page_size": 10}})
        )
        assert {item["id"] for item in all_authorized["items"]} == {
            "historico-autorizado-a",
            "historico-autorizado-b",
        }

        selected_property = _result_json(
            await session.call_tool(
                "list_cattle_documents",
                {
                    "payload": {
                        "property_ids": [str(harness.fixture_ids["property_a"])],
                        "page_size": 10,
                    }
                },
            )
        )
        assert [item["id"] for item in selected_property["items"]] == ["historico-autorizado-a"]

        external_selection = await session.call_tool(
            "list_cattle_documents",
            {"payload": {"property_ids": [str(harness.fixture_ids["property_external"])]}},
        )
        assert external_selection.isError


async def test_exception_animals_and_applications_exclude_external_and_cross_property_rows(
    mcp_data_harness,
):
    harness = mcp_data_harness
    async with harness.client() as session:
        animals = _result_json(
            await session.call_tool(
                "list_exception_animals",
                {"payload": {"statuses": ["ATIVO"], "page_size": 20}},
            )
        )
        assert {item["animal_id"] for item in animals["items"]} == {
            str(harness.fixture_ids["animal_a"]),
            str(harness.fixture_ids["animal_b"]),
        }
        assert {item["ear_tag"] for item in animals["items"]} == {"BRINCO-IGUAL"}

        collective = _result_json(
            await session.call_tool(
                "list_applications",
                {"payload": {"application_kind": "COLETIVA", "page_size": 20}},
            )
        )
        assert len(collective["items"]) == 2
        assert all(item["application_kind"] == "COLETIVA" for item in collective["items"])

        individual = _result_json(
            await session.call_tool(
                "list_applications",
                {"payload": {"application_kind": "INDIVIDUAL", "page_size": 20}},
            )
        )
        assert {item["application_id"] for item in individual["items"]} == {
            str(harness.fixture_ids["individual_a"]),
            str(harness.fixture_ids["individual_b"]),
        }

        details = _result_json(
            await session.call_tool("list_application_animals", {"payload": {"page_size": 20}})
        )
        assert {item["animal_id"] for item in details["items"]} == {
            str(harness.fixture_ids["animal_a"]),
            str(harness.fixture_ids["animal_b"]),
        }


async def test_occurrences_and_lot_history_exclude_external_property_rows(mcp_data_harness):
    harness = mcp_data_harness
    async with harness.client() as session:
        day = str(harness.fixture_ids["today"])
        occurrences = _result_json(
            await session.call_tool(
                "list_occurrences",
                {"payload": {"date_from": day, "date_to": day, "page_size": 20}},
            )
        )
        assert {item["animal_id"] for item in occurrences["items"]} == {
            str(harness.fixture_ids["animal_a"]),
            str(harness.fixture_ids["animal_b"]),
        }

        history = _result_json(
            await session.call_tool(
                "list_animal_lot_history",
                {"payload": {"date_from": day, "date_to": day, "page_size": 20}},
            )
        )
        assert {item["animal_id"] for item in history["items"]} == {
            str(harness.fixture_ids["animal_a"]),
            str(harness.fixture_ids["animal_b"]),
        }
        assert all(
            item["property_id"]
            in {
                str(harness.fixture_ids["property_a"]),
                str(harness.fixture_ids["property_b"]),
            }
            for item in history["items"]
        )


async def test_missing_invalid_and_expired_delegation_fail_before_mcp(mcp_data_harness):
    import httpx

    harness = mcp_data_harness
    headers = {"Authorization": f"Bearer {harness.service_token}"}
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        missing = await client.get(harness.url, headers=headers)
        invalid = await client.get(
            harness.url, headers={**headers, "X-Argos-Delegation": "not-a-token"}
        )
        expired = await client.get(
            harness.url,
            headers={
                **headers,
                "X-Argos-Delegation": harness.delegation_token({"exp": int(time.time()) - 1}),
            },
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert expired.status_code == 401


async def test_agent_and_operation_mismatch_are_denied_before_repository(mcp_data_harness):
    harness = mcp_data_harness
    async with harness.client(delegation_overrides={"operations": ["report.read"]}) as session:
        denied_operation = await session.call_tool("list_properties", {"payload": {}})
        assert denied_operation.isError

    async with harness.client(
        delegation_overrides={"agent": "doutor", "operations": ["medicine.read"]}
    ) as session:
        denied_agent = await session.call_tool("list_properties", {"payload": {}})
        assert denied_agent.isError


async def test_revoked_or_inactive_property_invalidates_the_whole_delegation(
    mcp_data_harness,
):
    harness = mcp_data_harness
    sql = await harness.connect_to_test_database()
    try:
        async with harness.client() as session:
            # The first page proves the delegation was initially usable.
            first_page = await session.call_tool("list_properties", {"payload": {"page_size": 1}})
            assert not first_page.isError

            # Deactivate one of the properties still named by the signed token.
            # Authorization must reject the stale scope before running SQL tools.
            property_inactive = await sql.execute(
                "UPDATE property SET active = FALSE WHERE id = $1",
                harness.fixture_ids["property_a"],
            )
            assert property_inactive == "UPDATE 1"
            denied_inactive = await session.call_tool(
                "list_properties", {"payload": {"page_size": 1, "offset": 1}}
            )
            assert denied_inactive.isError

            await sql.execute(
                "UPDATE property SET active = TRUE WHERE id = $1",
                harness.fixture_ids["property_a"],
            )
            link_revoked = await sql.execute(
                "UPDATE user_property SET active = FALSE WHERE user_id = $1 AND property_id = $2",
                harness.fixture_ids["user_id"],
                harness.fixture_ids["property_b"],
            )
            assert link_revoked == "UPDATE 1"
            denied_revoked = await session.call_tool(
                "list_properties", {"payload": {"page_size": 1, "offset": 1}}
            )
            assert denied_revoked.isError
    finally:
        await sql.close()
