"""Normalização de argumentos e respostas das ferramentas MCP."""

import json
from typing import Any


def _mcp_result_text(result: Any) -> str:
    """Retorna conteúdo útil ao agente sem expor envelope interno do transporte."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, default=str)
    content = getattr(result, "content", ())
    text_blocks = [text for block in content if (text := getattr(block, "text", None))]
    if text_blocks:
        return "\n".join(text_blocks)
    return "O MCP retornou uma resposta vazia."
