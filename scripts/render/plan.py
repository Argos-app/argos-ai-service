"""Seleciona deploys independentes do Render a partir da última publicação bem-sucedida."""

import json
import os
import subprocess
from pathlib import PurePosixPath

SERVICES = ("main_api", "mcp_data", "mcp_email", "mcp_prediction", "a2a_agent")
SHARED_FILES = {"pyproject.toml", "pylock.toml", ".python-version", "render.yaml"}
SHARED_PREFIXES = ("packages/", "scripts/render/", ".github/workflows/deploy.yml")


def affected_services(paths: list[str]) -> list[str]:
    paths = [path for path in paths if not is_documentation(path)]
    if any(path in SHARED_FILES or path.startswith(SHARED_PREFIXES) for path in paths):
        return list(SERVICES)
    return [service for service in SERVICES if any(p.startswith(f"apps/{service}/") for p in paths)]


def is_documentation(path: str) -> bool:
    file = PurePosixPath(path)
    name = file.name.lower()
    if name.startswith(("openapi", "swagger")) and file.suffix.lower() in {
        ".json",
        ".yaml",
        ".yml",
    }:
        return False
    return (
        file.suffix.lower() in {".md", ".mdx", ".markdown", ".rst", ".adoc"} or "docs" in file.parts
    )


def changed_paths(base: str, head: str) -> list[str]:
    """Não detecta renomes: mover arquivos entre aplicações exige publicar ambos os consumidores."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8").split("\0")[:-1]


def main() -> None:
    branch = os.environ["DEPLOY_BRANCH"]
    if branch not in {"develop", "main"}:
        raise SystemExit("Somente as branches develop e main podem ser implantadas.")
    base = os.environ.get("BASE_SHA", "")
    services = list(SERVICES)
    if base and os.environ.get("FORCE_ALL") != "true":
        services = affected_services(changed_paths(base, os.environ["DEPLOY_SHA"]))
    outputs = {
        "services": json.dumps(services),
        "has_changes": str(bool(services)).lower(),
        "environment": "hm" if branch == "develop" else "prod",
    }
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    print(json.dumps(outputs))


if __name__ == "__main__":
    main()
