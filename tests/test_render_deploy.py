"""Seleção de publicações, pontos de entrada nativos e tratamento de falhas da API do Render."""

import json
import os
import subprocess
import sys
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from scripts.render import deploy, plan, start

SHA = "a" * 40
SERVICE = {
    "branch": "develop",
    "rootDir": "",
    "autoDeploy": "no",
    "autoDeployTrigger": "off",
    "serviceDetails": {"runtime": "python"},
}


@pytest.mark.parametrize("service", plan.SERVICES)
def test_application_change_only_deploys_that_service(service):
    assert plan.affected_services([f"apps/{service}/requirements.txt"]) == [service]


@pytest.mark.parametrize(
    "path",
    [
        "packages/contracts/lot_report.py",
        "packages/new_package/module.py",
        "scripts/render/build.sh",
        ".github/workflows/deploy.yml",
        ".python-version",
        "pyproject.toml",
        "pylock.toml",
    ],
)
def test_shared_changes_deploy_all_consumers(path):
    assert plan.affected_services([path]) == list(plan.SERVICES)


def test_documentation_and_local_infrastructure_do_not_deploy():
    assert plan.affected_services(["README.md", "docs/api.md", "compose.yaml"]) == []


def test_git_range_includes_multiple_commits_deletions_and_cross_service_moves(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Render Test",
                "-c",
                "user.email=render@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            text=True,
        ).strip()

    git("init", "--quiet")
    original = tmp_path / "apps/mcp_data/module.py"
    original.parent.mkdir(parents=True)
    original.write_text("original\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "baseline")
    base = git("rev-parse", "HEAD")
    destination = tmp_path / "apps/mcp_email/module.py"
    destination.parent.mkdir(parents=True)
    original.rename(destination)
    git("add", "--all")
    git("commit", "--quiet", "-m", "move")
    api = tmp_path / "apps/main_api/module.py"
    api.parent.mkdir(parents=True)
    api.write_text("next change\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "next push")
    paths = plan.changed_paths(base, git("rev-parse", "HEAD"))
    assert plan.affected_services(paths) == ["main_api", "mcp_data", "mcp_email"]


@pytest.mark.parametrize("branch,environment", [("develop", "hm"), ("main", "prod")])
@pytest.mark.parametrize("force,base", [("false", ""), ("true", SHA)])
def test_initial_and_manual_rollouts_deploy_every_service(
    branch, environment, force, base, tmp_path, monkeypatch
):
    output = tmp_path / "outputs"
    for key, value in {
        "DEPLOY_BRANCH": branch,
        "BASE_SHA": base,
        "FORCE_ALL": force,
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    plan.main()
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["environment"] == environment
    assert json.loads(values["services"]) == list(plan.SERVICES)


def test_unrelated_branch_cannot_select_an_environment(monkeypatch):
    monkeypatch.setenv("DEPLOY_BRANCH", "feature/cloud")
    with pytest.raises(SystemExit, match="Only develop and main"):
        plan.main()


@pytest.mark.parametrize("service", plan.SERVICES)
def test_native_entrypoints_resolve_shared_and_application_code(service):
    env = dict(os.environ, PORT="12345")
    args = start.command(service, env)
    module = "app" if service == "main_api" else start.MODULES[service]
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib.util; import packages.contracts; "
            f"assert importlib.util.find_spec('{module}') is not None",
        ],
        env=env,
        cwd=start.ROOT,
        check=True,
    )
    if service == "main_api":
        assert args[-4:] == ["--host", "0.0.0.0", "--port", "12345"]
    else:
        prefix = "A2A" if service == "a2a_agent" else service.upper()
        assert env[f"{prefix}_PORT"] == "12345"
        assert env[f"{prefix}_HOST"] == "0.0.0.0"


@pytest.mark.parametrize(
    "overrides",
    [
        {"branch": "main"},
        {"rootDir": "apps/main_api"},
        {"serviceDetails": {"runtime": "docker"}},
        {"serviceDetails": {"runtime": "image"}},
        {"autoDeployTrigger": "commit"},
        {"autoDeploy": "yes"},
    ],
)
def test_wrong_environment_or_build_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        deploy.validate_service(SERVICE | overrides, "develop")


def test_deployment_sends_exact_commit_and_waits_for_live(monkeypatch):
    responses = iter(
        [
            SERVICE,
            {"id": "dep-test"},
            {"status": "queued"},
            {"status": "build_in_progress"},
            {"status": "live", "commit": {"id": SHA}},
        ]
    )
    calls = []

    def request(*args):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(deploy, "request", request)
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    assert deploy.deploy("srv-test", "token", SHA, "develop").endswith("/dep-test")
    assert calls[1] == ("POST", "services/srv-test/deploys", "token", {"commitId": SHA})
    assert len(calls) == 5


@pytest.mark.parametrize(
    "result",
    [
        {"status": "build_failed"},
        {"status": "canceled"},
        {"status": "pre_deploy_failed"},
        {"status": "live", "commit": {"id": "b" * 40}},
    ],
)
def test_failed_deploy_or_wrong_live_commit_does_not_report_success(result, monkeypatch):
    responses = iter([SERVICE, {"id": "dep-test"}, result])
    monkeypatch.setattr(deploy, "request", lambda *args: next(responses))
    with pytest.raises(RuntimeError):
        deploy.deploy("srv-test", "token", SHA, "develop")


def test_timeout_reports_deployment_link(monkeypatch):
    responses = iter([SERVICE, {"id": "dep-test"}])
    monkeypatch.setattr(deploy, "request", lambda *args: next(responses))
    with pytest.raises(RuntimeError, match="Timed out.*dep-test"):
        deploy.deploy("srv-test", "token", SHA, "develop", timeout=0)


@pytest.mark.parametrize(
    "failure", [URLError("private detail"), HTTPError(deploy.API, 503, "", {}, None)]
)
def test_ambiguous_trigger_is_never_retried_or_exposed(failure, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise failure

    monkeypatch.setattr(deploy, "urlopen", fail)
    with pytest.raises(RuntimeError) as error:
        deploy.request("POST", "services/srv-test/deploys", "secret-token", {"commitId": SHA})
    assert len(calls) == 1
    assert "secret-token" not in str(error.value)
    assert "private detail" not in str(error.value)


def test_transient_get_failure_is_retried(monkeypatch):
    responses = iter([HTTPError(deploy.API, 429, "", {}, None), BytesIO(b'{"status": "live"}')])

    def urlopen(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(deploy, "urlopen", urlopen)
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    assert deploy.request("GET", "services/srv-test/deploys/dep-test", "token") == {
        "status": "live"
    }
