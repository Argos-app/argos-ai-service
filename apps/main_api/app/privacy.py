"""Sanitização compartilhada antes de persistir ou enviar conteúdo ao LLM."""

import re

EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
CPF_PATTERN = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")


def minimize_pii(text: str) -> str:
    """Remove os identificadores pessoais cobertos pelo guardrail atual."""
    return CPF_PATTERN.sub("[CPF]", EMAIL_PATTERN.sub("[EMAIL]", text))
