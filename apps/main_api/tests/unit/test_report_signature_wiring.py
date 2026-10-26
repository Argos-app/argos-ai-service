"""Wiring da verificação de assinatura na construção do grafo HTTP."""

import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_graph_provider_injects_report_source_signature_verifier(monkeypatch):
    service_dependencies = import_module("app.api.v1.dependencies.services")
    specialist_module = import_module("app.agents.specialists")
    workflow_module = import_module("app.graph.workflow")
    verifier = object()
    captured = {}
    agent_factory = object()
    graph = object()

    def build_agent_factory(_models, **kwargs):
        captured.update(kwargs)
        return agent_factory

    def build_graph(factory, **kwargs):
        assert factory is agent_factory
        assert kwargs["report_artifacts"] is persistence.report_artifacts
        return graph

    monkeypatch.setattr(specialist_module, "build_agent_factory", build_agent_factory)
    monkeypatch.setattr(workflow_module, "build_graph", build_graph)

    persistence = SimpleNamespace(
        execution_events=None,
        report_artifacts=object(),
        email_preparations=object(),
    )
    app_state = SimpleNamespace(
        agent_graph=None,
        observability_collector=None,
        models={},
        persistence=persistence,
        mcp_clients=None,
        documental_a2a_client=None,
        mcp_delegation_token_issuer=None,
        checkpointer=None,
        report_source_signature_verifier=verifier,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=app_state))

    assert service_dependencies._graph_provider(request) is graph
    assert captured["report_signature_checker"] is verifier
