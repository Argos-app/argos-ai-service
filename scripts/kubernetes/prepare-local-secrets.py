"""Converte o .env local oficial para o env file do Secret Kustomize."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REQUIRED = {
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "POSTGRES_READER_PASSWORD",
    "MONGO_DATABASE",
    "MCP_DELEGATION_ISSUER",
    "MCP_DELEGATION_KEY_ID",
    "MCP_DELEGATION_PRIVATE_KEY",
    "MCP_DELEGATION_PUBLIC_KEYS",
    "REPORT_SIGNING_KEY_ID",
    "REPORT_SIGNING_PRIVATE_KEY",
    "REPORT_SIGNING_PUBLIC_KEYS",
    "MCP_DATA_INTERNAL_SERVICE_TOKEN",
    "MCP_PREDICTION_INTERNAL_SERVICE_TOKEN",
    "MCP_EMAIL_INTERNAL_SERVICE_TOKEN",
    "A2A_INTERNAL_SERVICE_TOKEN",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if not separator or not name.replace("_", "").isalnum():
            raise ValueError(f"linha dotenv inválida: {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    missing = sorted(REQUIRED - values.keys())
    if missing:
        raise ValueError("variáveis ausentes: " + ", ".join(missing))
    if any(not values[key] for key in REQUIRED):
        raise ValueError("há variáveis obrigatórias sem valor")
    return {key: values[key] for key in sorted(REQUIRED)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("k8s/local/secret.env"))
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_file():
        parser.error(f"arquivo de origem não existe: {source}")
    values = parse_env(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# Secret local descartável; não adicionar ao Git.\n"
        + "\n".join(f"{name}={value}" for name, value in values.items())
        + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"Arquivo Secret local preparado: {output}")


if __name__ == "__main__":
    main()
