"""Formata resultados de ferramentas quando a síntese por modelo não termina."""

import json
from collections.abc import Sequence

from app.application.execution_limits import ToolEvidence

MAX_FALLBACK_EVIDENCE_CHARS = 7_000


def verified_evidence_fallback(
    evidence: Sequence[ToolEvidence],
    *,
    unavailable_stage: str,
) -> str | None:
    """Expõe somente saídas verificadas de ferramentas, sem inventar uma síntese."""
    if unavailable_stage not in {"síntese", "validação", "processamento"}:
        raise ValueError("etapa indisponível inválida")
    blocks: list[str] = []
    remaining = MAX_FALLBACK_EVIDENCE_CHARS
    truncated = any(item.truncated for item in evidence)
    for item in evidence:
        if remaining <= 0:
            truncated = True
            break
        try:
            decoded = json.loads(item.excerpt)
        except TypeError, ValueError:
            rendered = item.excerpt
        else:
            rendered = json.dumps(decoded, ensure_ascii=False, indent=2, default=str)
        full_block = f"{item.tool_name}:\n{rendered}"
        block = full_block[:remaining]
        truncated = truncated or len(block) < len(full_block)
        if block.strip():
            blocks.append(block)
            remaining -= len(block)
    if not blocks:
        return None
    return (
        f"A etapa de {unavailable_stage} pelo modelo não foi concluída. "
        "Abaixo estão os resultados estruturados retornados diretamente pelas "
        "ferramentas; eles não receberam interpretação adicional:\n\n"
        + "\n\n".join(blocks)
        + (
            "\n\nAviso: os resultados estão incompletos porque parte das evidências foi limitada. "
            "Repita a consulta com filtros mais específicos."
            if truncated
            else ""
        )
    )
