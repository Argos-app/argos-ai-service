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


def test_readiness_requires_authentication_keys_and_graph_runtime(monkeypatch) -> None:
    main_module = import_module("app.main")
    runtime_checkpointer = object()

    class Runtime:
        persistence = object()
        database = type("Database", (), {"command": lambda self, name: _ping()})()

        async def close(self):
            pass

    async def create_runtime(_settings):
        runtime = Runtime()
        runtime.checkpointer = runtime_checkpointer
        return runtime

    async def _ping():
        return {"ok": 1}

    monkeypatch.setattr(main_module, "create_runtime", create_runtime)
    Settings = import_module("app.settings").Settings

    with TestClient(
        main_module.create_app(Settings(mongo_uri="mongodb://test"))
    ) as identity_missing:
        assert identity_missing.get("/health/ready").json()["reason"] == "identity_not_configured"

    with TestClient(
        main_module.create_app(
            Settings(mongo_uri="mongodb://test", auth_trusted_public_keys=(("kid", "key"),)),
        )
    ) as ready:
        assert ready.get("/health/ready").json() == {"status": "ok"}

    runtime_checkpointer = None
    with TestClient(
        main_module.create_app(
            Settings(mongo_uri="mongodb://test", auth_trusted_public_keys=(("kid", "key"),)),
        )
    ) as no_runtime:
        response = no_runtime.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "graph_runtime_not_configured"


def test_planned_routes_are_present() -> None:
    paths = app.openapi()["paths"]
    expected = {
        ("/v1/sessions", "post"),
        ("/v1/sessions", "get"),
        ("/v1/sessions/{session_id}/messages", "get"),
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
