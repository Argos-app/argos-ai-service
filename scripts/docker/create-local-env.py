"""Gera credenciais descartáveis para a pilha Docker de desenvolvimento."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _key_pair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_bytes).decode("ascii"),
        base64.b64encode(public_bytes).decode("ascii"),
    )


def _dotenv(name: str, value: str) -> str:
    if value.startswith("{"):
        return f"{name}='{value}'"
    return f"{name}={value}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".env.local"),
        help="arquivo dotenv local (padrão: .env.local)",
    )
    parser.add_argument("--force", action="store_true", help="substitui o arquivo de destino")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and not args.force:
        parser.error(f"{output} já existe; use --force para substituí-lo")

    delegation_private, delegation_public = _key_pair()
    report_private, report_public = _key_pair()
    values = {
        "POSTGRES_USER": "argos_admin",
        "POSTGRES_DB": "argos",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "POSTGRES_READER_PASSWORD": secrets.token_urlsafe(32),
        "MONGO_DATABASE": "argos_ai",
        "MCP_DELEGATION_ISSUER": "argos-main-service",
        "MCP_DELEGATION_KEY_ID": "local-dev-key",
        "MCP_DELEGATION_PRIVATE_KEY": delegation_private,
        "MCP_DELEGATION_PUBLIC_KEYS": json.dumps(
            {"local-dev-key": delegation_public}, separators=(",", ":")
        ),
        "REPORT_SIGNING_KEY_ID": "local-report-key",
        "REPORT_SIGNING_PRIVATE_KEY": report_private,
        "REPORT_SIGNING_PUBLIC_KEYS": json.dumps(
            {"local-report-key": report_public}, separators=(",", ":")
        ),
        "MCP_DATA_INTERNAL_SERVICE_TOKEN": secrets.token_urlsafe(32),
        "MCP_PREDICTION_INTERNAL_SERVICE_TOKEN": secrets.token_urlsafe(32),
        "MCP_EMAIL_INTERNAL_SERVICE_TOKEN": secrets.token_urlsafe(32),
        "A2A_INTERNAL_SERVICE_TOKEN": secrets.token_urlsafe(32),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# Segredos aleatórios descartáveis. Somente para Docker local.\n"
        + "\n".join(_dotenv(name, value) for name, value in values.items())
        + "\n",
        encoding="utf-8",
    )
    try:
        output.chmod(0o600)
    except OSError:
        pass
    print(f"Arquivo local criado: {output}")


if __name__ == "__main__":
    main()
