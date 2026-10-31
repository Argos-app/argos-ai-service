"""Gera locks PEP 751 para Linux e Python 3.14.3, usados pelo runtime e pela CI."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCKS = {
    "apps/main_api/pylock.toml": ("apps/main_api/requirements.txt",),
    "apps/mcp_data/pylock.toml": ("apps/mcp_data/requirements.txt",),
    "apps/mcp_email/pylock.toml": ("apps/mcp_email/requirements.txt",),
    "apps/mcp_prediction/pylock.toml": ("apps/mcp_prediction/requirements.txt",),
    "apps/a2a_agent/pylock.toml": ("apps/a2a_agent/requirements.txt",),
    "pylock.toml": ("requirements-dev.txt",),
}


def main() -> None:
    if sys.platform != "linux":
        raise SystemExit("gere os locks em Linux para corresponder aos builds Docker e ao CI")
    if sys.version_info[:3] != (3, 14, 3):
        raise SystemExit("gere os locks com Python 3.14.3 em Linux (python:3.14.3-slim)")
    if version("pip") != "26.2.1":
        raise SystemExit("gere os locks com pip==26.2.1")

    for output, requirements in LOCKS.items():
        command = [sys.executable, "-m", "pip", "lock"]
        for requirement in requirements:
            command.extend(("--requirement", str(ROOT / requirement)))
        command.extend(("--output", str(ROOT / output)))
        subprocess.run(command, cwd=ROOT, check=True)
        print(f"Atualizado: {output}")


if __name__ == "__main__":
    main()
