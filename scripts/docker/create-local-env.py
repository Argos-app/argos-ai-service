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
        default=Path(".env"),
        help="arquivo dotenv oficial do ambiente local (padrão: .env)",
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
        "ARGOS_MODEL_MODE": "simulated",
        "MODEL_INPUT_COST_PER_MILLION_TOKENS": "",
        "MODEL_OUTPUT_COST_PER_MILLION_TOKENS": "",
        "A2A_QDRANT_VECTOR_SIZE": "64",
        "MAIN_API_MODEL_API_BASE_URL": "",
        "MAIN_API_MODEL_API_KEY": "",
        "MAIN_API_MODEL_ROUTER": "",
        "MAIN_API_MODEL_FARM": "",
        "MAIN_API_MODEL_DOCTOR": "",
        "MAIN_API_MODEL_ANALYST": "",
        "MAIN_API_MODEL_SECRETARY": "",
        "MAIN_API_MODEL_INFORMATION": "",
        "MAIN_API_MODEL_ORCHESTRATOR": "",
        "MAIN_API_MODEL_JUDGE": "",
        "MAIN_API_MODEL_QUESTION_FORMATTER": "",
        "A2A_EMBEDDINGS_API_URL": "",
        "A2A_EMBEDDINGS_API_KEY": "",
        "A2A_EMBEDDINGS_MODEL": "",
        "A2A_EMBEDDINGS_MODEL_REVISION": "",
        "A2A_ANSWER_API_URL": "",
        "A2A_ANSWER_API_KEY": "",
        "A2A_ANSWER_MODEL": "",
        "MCP_EMAIL_SMTP_HOST": "",
        "MCP_EMAIL_SMTP_PORT": "1025",
        "MCP_EMAIL_SMTP_USERNAME": "",
        "MCP_EMAIL_SMTP_PASSWORD": "",
        "MCP_EMAIL_SMTP_USE_TLS": "false",
        "MCP_EMAIL_NO_REPLY_ADDRESS": "no-reply@argos.local",
        "MAIN_API_LANGSMITH_API_KEY": "",
        "MAIN_API_LANGSMITH_PROJECT": "argos-ai-service",
        "MAIN_API_LANGSMITH_ENDPOINT": "",
        "MAIN_API_LANGSMITH_WORKSPACE_ID": "",
        "MAIN_API_LANGSMITH_CAPTURE_CONTENT": "false",
        "A2A_LANGSMITH_API_KEY": "",
        "A2A_LANGSMITH_PROJECT": "argos-ai-service",
        "A2A_LANGSMITH_ENDPOINT": "",
        "A2A_LANGSMITH_WORKSPACE_ID": "",
        "A2A_LANGSMITH_CAPTURE_CONTENT": "false",
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
