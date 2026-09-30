"""Verificações do contrato HTTP antes das integrações externas."""

import sys
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

app = import_module("app.main").app
MessageInteraction = import_module("app.api.v1.models.interactions").MessageInteraction
QuestionResponse = import_module("app.api.v1.models.interactions").QuestionResponse


client = TestClient(app)


def test_health_and_readiness() -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 503


def test_planned_routes_are_present() -> None:
    paths = app.openapi()["paths"]
    expected = {
        ("/v1/sessions", "post"),
        ("/v1/sessions", "get"),
        ("/v1/sessions/{session_id}/interactions", "post"),
    }
    assert all(method in paths[path] for path, method in expected)
    assert "/v1/sessions/{session_id}/answers" not in paths
    assert "/v1/sessions/{session_id}" not in paths
    assert "/v1/sessions/{session_id}/interactions/{interaction_id}" not in paths
    assert "/v1/sessions/{session_id}/interactions/{interaction_id}/cancel" not in paths
    assert "requestBody" not in paths["/v1/sessions"]["post"]


def test_session_routes_do_not_expose_pagination_parameters() -> None:
    paths = app.openapi()["paths"]
    for path in ("/v1/sessions",):
        parameters = paths[path]["get"].get("parameters", [])
        assert not {parameter["name"] for parameter in parameters} & {"limit", "cursor"}


def test_protected_route_rejects_missing_bearer() -> None:
    response = client.get("/v1/sessions")
    assert response.status_code == 401


def test_protected_route_does_not_claim_success_without_authentication() -> None:
    response = client.get("/v1/sessions", headers={"Authorization": "Bearer placeholder"})
    assert response.status_code == 503


def test_interaction_rejects_oversized_message() -> None:
    with pytest.raises(ValidationError):
        MessageInteraction.model_validate(
            {
                "kind": "message",
                "client_message_id": str(uuid4()),
                "message": "x" * 8001,
            }
        )


def test_single_choice_question_requires_unique_options() -> None:
    question = {
        "kind": "question",
        "question_id": str(uuid4()),
        "input_type": "single_choice",
        "prompt": "Qual período?",
        "reason_code": "MISSING_PERIOD",
        "options": [
            {"option_id": "7d", "label": "Últimos 7 dias"},
            {"option_id": "30d", "label": "Últimos 30 dias"},
        ],
        "expires_at": "2026-10-01T12:00:00Z",
    }
    assert QuestionResponse.model_validate(question).options[1].option_id == "30d"

    question["options"] = []
    with pytest.raises(ValidationError):
        QuestionResponse.model_validate(question)

    question["options"] = [
        {"option_id": "7d", "label": "Últimos 7 dias"},
        {"option_id": "7d", "label": "Outro rótulo"},
    ]
    with pytest.raises(ValidationError):
        QuestionResponse.model_validate(question)
