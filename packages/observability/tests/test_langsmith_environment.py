import pytest

from packages.observability.langsmith_environment import resolve_langsmith_agent_environment


@pytest.mark.parametrize(
    ("configured", "app_env", "expected"),
    [
        (None, "local", "local"),
        (None, "development", "development"),
        (None, "staging", "staging"),
        (None, "production", "production"),
        ("local", "production", "local"),
        ("development", "production", "development"),
    ],
)
def test_resolve_langsmith_agent_environment(configured, app_env, expected):
    assert resolve_langsmith_agent_environment(configured, app_env) == expected


def test_resolve_langsmith_agent_environment_rejects_unknown_values():
    with pytest.raises(ValueError, match="LangSmith Agent environment"):
        resolve_langsmith_agent_environment("qa", "production")


@pytest.mark.parametrize("alias", ["dev", "develop", "prod"])
def test_resolve_langsmith_agent_environment_rejects_aliases(alias):
    with pytest.raises(ValueError, match="LangSmith Agent environment"):
        resolve_langsmith_agent_environment(None, alias)
