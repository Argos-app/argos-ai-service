from datetime import date
from uuid import uuid4

import pytest
from app.infrastructure.a2a.langchain_tool import build_document_a2a_tool

from packages.contracts.a2a import DocumentSkill


class FakeDocumentClient:
    def __init__(self) -> None:
        self.arguments = None

    async def ask(
        self,
        question,
        *,
        user_id,
        skill,
        jurisdiction,
        reference_date,
    ):
        self.arguments = {
            "question": question,
            "user_id": user_id,
            "skill": skill,
            "jurisdiction": jurisdiction,
            "reference_date": reference_date,
        }
        return '{"status":"partial"}'


@pytest.mark.asyncio
async def test_legal_tool_passes_scope_jurisdiction_and_reference_date():
    client = FakeDocumentClient()
    tool = build_document_a2a_tool(client, skill=DocumentSkill.LEGAL)
    user_id = str(uuid4())

    result = await tool.ainvoke(
        {
            "question": "O que diz a lei sobre registros?",
            "jurisdiction": "BR",
            "reference_date": "2025-01-15",
        },
        config={"configurable": {"argos_user_id": user_id}},
    )

    assert result == '{"status":"partial"}'
    assert client.arguments == {
        "question": "O que diz a lei sobre registros?",
        "user_id": user_id,
        "skill": DocumentSkill.LEGAL,
        "jurisdiction": "BR",
        "reference_date": date(2025, 1, 15),
    }
    assert set(tool.args_schema.model_fields) == {
        "question",
        "jurisdiction",
        "reference_date",
    }


@pytest.mark.asyncio
async def test_documental_tool_does_not_expose_legal_arguments():
    client = FakeDocumentClient()
    tool = build_document_a2a_tool(client)
    user_id = str(uuid4())

    await tool.ainvoke(
        {"question": "O que dizem os termos sobre relatórios?"},
        config={"configurable": {"argos_user_id": user_id}},
    )

    assert tool.name == "consultar_documental"
    assert set(tool.args_schema.model_fields) == {"question"}
    assert client.arguments["skill"] == DocumentSkill.DOCUMENTAL
    assert client.arguments["jurisdiction"] is None


@pytest.mark.asyncio
async def test_legal_tool_normalizes_provider_null_strings():
    client = FakeDocumentClient()
    tool = build_document_a2a_tool(client, skill=DocumentSkill.LEGAL)

    await tool.ainvoke(
        {
            "question": "A norma tem uma data de referência?",
            "jurisdiction": "BR",
            "reference_date": "None",
        },
        config={"configurable": {"argos_user_id": str(uuid4())}},
    )

    assert client.arguments["jurisdiction"] == "BR"
    assert client.arguments["reference_date"] is None
