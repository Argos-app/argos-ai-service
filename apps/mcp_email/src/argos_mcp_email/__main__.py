"""Entrada de processo do MCP de e-mail."""

import asyncio
import importlib
import os
from collections.abc import Callable
from typing import Any

from argos_mcp_email.bootstrap import run_server


def _load_signature_checker() -> Callable[[bytes, str, str], bool]:
    """Carrega o verificador criptográfico fornecido pela implantação.

    O valor deve ser ``pacote.modulo:nome_da_funcao``. Assim a entrada do
    processo não escolhe nem inventa uma política de chaves.
    """

    target = os.getenv("MCP_EMAIL_SIGNATURE_VERIFIER")
    if not target or ":" not in target:
        raise RuntimeError("MCP_EMAIL_SIGNATURE_VERIFIER deve apontar para modulo:funcao")
    module_name, function_name = target.split(":", 1)
    verifier: Any = getattr(importlib.import_module(module_name), function_name, None)
    if not callable(verifier):
        raise RuntimeError(f"verificador de assinatura inválido: {target}")
    return verifier


def main() -> None:
    asyncio.run(run_server(signature_checker=_load_signature_checker()))


if __name__ == "__main__":
    main()
