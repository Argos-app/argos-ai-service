"""Start one native Render service with the monorepo's shared packages available."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "mcp_data": "argos_mcp_data",
    "mcp_email": "argos_mcp_email",
    "mcp_prediction": "argos_mcp_prediction",
    "a2a_agent": "argos_a2a_agent",
}


def command(service: str, env: dict[str, str]) -> list[str]:
    if service != "main_api" and service not in MODULES:
        raise ValueError("Unknown application.")
    port = int(env.get("PORT", "10000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535.")
    app_path = ROOT / "apps" / service
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(app_path if service == "main_api" else app_path / "src")]
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if service == "main_api":
        return [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]
    prefix = "A2A" if service == "a2a_agent" else service.upper()
    env[f"{prefix}_HOST"] = "0.0.0.0"
    env[f"{prefix}_PORT"] = str(port)
    if service == "a2a_agent":
        env.setdefault("A2A_DOCUMENT_SOURCE_ROOT", str(app_path / "data" / "documents"))
        env.setdefault("A2A_MANIFEST_PATH", "/tmp/argos-a2a/manifest.json")
    return [sys.executable, "-m", MODULES[service]]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/render/start.py <application>")
    env = dict(os.environ)
    args = command(sys.argv[1], env)
    os.chdir(ROOT)
    os.execvpe(args[0], args, env)


if __name__ == "__main__":
    main()
