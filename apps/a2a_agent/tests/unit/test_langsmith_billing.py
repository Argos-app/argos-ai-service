from argos_a2a_agent.adapters.observability.langsmith import attach_provider_billing

from packages.observability import configured_rate_billing, provider_billing


def test_a2a_trace_gets_provider_cost_and_native_usd_usage(monkeypatch):
    calls = []

    class RunTree:
        def set(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("langsmith.run_helpers.get_current_run_tree", lambda: RunTree())
    billing = provider_billing({"usage": {"total_cost_usd": "0.0025"}})

    attach_provider_billing(
        billing,
        {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
    )

    assert calls == [
        {
            "metadata": {
                "argos_billing": {
                    "reported_cost": "0.0025",
                    "currency": "USD",
                    "cost_source": "provider",
                }
            },
            "usage_metadata": {
                "input_tokens": 12,
                "output_tokens": 4,
                "total_tokens": 16,
                "total_cost": 0.0025,
            },
        }
    ]


def test_a2a_cost_annotation_is_noop_outside_a_trace(monkeypatch):
    monkeypatch.setattr("langsmith.run_helpers.get_current_run_tree", lambda: None)
    billing = provider_billing({"usage": {"total_cost_usd": "0.0025"}})

    attach_provider_billing(billing)


def test_a2a_langsmith_metadata_supports_configured_rate_estimate(monkeypatch):
    calls = []

    class RunTree:
        def set(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("langsmith.run_helpers.get_current_run_tree", lambda: RunTree())
    monkeypatch.setenv("MODEL_INPUT_COST_PER_MILLION_TOKENS", "2.5")
    billing = configured_rate_billing(input_tokens=200, output_tokens=None, embedding=True)

    attach_provider_billing(
        billing,
        {"input_tokens": 200, "total_tokens": 200},
        embedding=True,
    )

    assert calls[0]["metadata"]["argos_billing"] == {
        "reported_cost": "0.0005",
        "currency": "USD",
        "cost_source": "configured_rate",
    }
    assert calls[0]["usage_metadata"] == {
        "input_tokens": 200,
        "total_tokens": 200,
        "total_cost": 0.0005,
        "input_cost": 0.0005,
    }
