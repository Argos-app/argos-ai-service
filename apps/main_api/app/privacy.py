"""Sanitização compartilhada antes de persistir ou enviar conteúdo ao LLM."""

import re

EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
CPF_PATTERN = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:bearer|password|passwd|secret|api[_-]?key|access[_-]?token|token)\b\s*[:= ]\s*)"
    r"([^\s,;]+)"
)


def minimize_pii(text: str) -> str:
    """Remove os identificadores pessoais cobertos pelo guardrail atual."""
    return CPF_PATTERN.sub("[CPF]", EMAIL_PATTERN.sub("[EMAIL]", text))


def minimize_sensitive_memory_input(text: str) -> str:
    """Remove PII e valores com aparência de credencial antes de extrair perfil."""
    return SECRET_VALUE_PATTERN.sub(r"\1[REDACTED]", minimize_pii(text))
