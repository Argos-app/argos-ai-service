"""Verificações do contrato HTTP antes das integrações externas."""

import json
import sys
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent_schemas.results import QuestionFormatterResult

app = import_module("app.main").app
ApplicationError = import_module("app.application.errors").ApplicationError
ApplicationErrorCode = import_module("app.application.errors").ApplicationErrorCode
create_app = import_module("app.main").create_app
MessageInteraction = import_module("app.api.v1.models.interactions").MessageInteraction
QuestionResponse = import_module("app.api.v1.models.interactions").QuestionResponse


client = TestClient(app)


def test_published_openapi_matches_application_schema() -> None:
    snapshot = Path(__file__).resolve().parents[2] / "docs" / "openapi-main-api.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == app.openapi()


class _FakeChatModel:
    def with_structured_output(self, _schema):
        return self

    def bind_tools(self, _tools):
        return self


def _fake_models():
    ModelPolicyId = import_module("app.agents.registry").ModelPolicyId
    return {policy: _FakeChatModel() for policy in ModelPolicyId}


def test_health_and_readiness() -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 503


def test_application_errors_keep_the_public_http_error_shape() -> None:
    application = create_app(agent_graph=object(), models=_fake_models())

    @application.get("/application-error-contract")
    async def raise_application_error():
        raise ApplicationError(ApplicationErrorCode.CONFLICT, "Conflito de teste")

    response = TestClient(application).get("/application-error-contract")

    assert response.status_code == 409
    assert response.json() == {"detail": "Conflito de teste"}


def test_readiness_requires_authentication_keys_and_graph_runtime(monkeypatch) -> None:
    main_module = import_module("app.main")
    runtime_checkpointer = object()

    class Runtime:
        persistence = object()
        user_usage_counter = None
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
            Settings(mongo_uri="mongodb://test", app_env="local", auth_mode="stub"),
            checkpointer=runtime_checkpointer,
        )
    ) as models_missing:
        response = models_missing.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "model_runtime_not_configured"

    with TestClient(
        main_module.create_app(
            Settings(mongo_uri="mongodb://test", app_env="local", auth_mode="stub"),
            models=_fake_models(),
            agent_graph=object(),
        )
    ) as configured:
        assert configured.get("/health/ready").json() == {"status": "ok"}

    runtime_checkpointer = None
    with TestClient(
        main_module.create_app(
            Settings(mongo_uri="mongodb://test", app_env="local", auth_mode="stub"),
            models=_fake_models(),
        )
    ) as no_runtime:
        response = no_runtime.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "graph_runtime_not_configured"


def test_planned_routes_are_present() -> None:
    paths = app.openapi()["paths"]
    expected = {
        ("/v1/auth/firebase", "post"),
        ("/v1/sessions", "post"),
        ("/v1/sessions", "get"),
        ("/v1/sessions/{session_id}/messages", "get"),
        ("/v1/sessions/{session_id}/interactions/async", "post"),
        ("/v1/sessions/{session_id}/interactions/{operation_id}", "get"),
    }
    assert all(method in paths[path] for path, method in expected)
    assert "/v1/sessions/{session_id}/interactions" not in paths
    async_interaction = paths["/v1/sessions/{session_id}/interactions/async"]["post"]
    assert "202" in async_interaction["responses"]
    assert async_interaction["responses"]["202"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/InteractionAccepted")
    firebase_auth = paths["/v1/auth/firebase"]["post"]
    assert "requestBody" not in firebase_auth
    assert firebase_auth["responses"]["422"]["description"] == "Erro de validação"
    assert firebase_auth["responses"]["422"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/HTTPValidationError")
    assert any(
        parameter["name"].casefold() == "authorization" and parameter["in"] == "header"
        for parameter in firebase_auth["parameters"]
    )
    assert firebase_auth["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/AccessTokenResponse")
    assert set(app.openapi()["components"]["schemas"]["AccessTokenResponse"]["properties"]) == {
        "access_token"
    }
    assert app.openapi()["components"]["schemas"]["InteractionResult"]["properties"]["status"][
        "enum"
    ] == ["completed", "degraded"]
    assert (
        "degradation_reasons"
        in app.openapi()["components"]["schemas"]["InteractionResult"]["properties"]
    )
    assert "/v1/sessions/{session_id}/answers" not in paths
    assert "/v1/sessions/{session_id}" not in paths
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


def test_question_formatter_can_return_general_choice_labels_without_ids() -> None:
    result = QuestionFormatterResult.model_validate(
        {
            "prompt": "Qual período você quer consultar?",
            "options": ["Últimos 7 dias", "Últimos 30 dias"],
        }
    )
    assert result.options == ("Últimos 7 dias", "Últimos 30 dias")

    with pytest.raises(ValidationError):
        QuestionFormatterResult.model_validate(
            {"prompt": "Qual período?", "options": ["Últimos 7 dias"]}
        )
