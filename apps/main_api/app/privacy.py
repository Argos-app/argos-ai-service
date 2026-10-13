"""Sanitização compartilhada antes de persistir ou enviar conteúdo ao LLM."""

import re
from dataclasses import dataclass

EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
CPF_PATTERN = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CNPJ_PATTERN = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?\(?[1-9]\d\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)")
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:bearer|password|passwd|secret|api[_-]?key|access[_-]?token|token)\b\s*[:= ]\s*)"
    r"([^\s,;]+)"
)
_SEND_VERB_PATTERN = re.compile(
    r"\b(?:envie|enviar|mande|mandar|encaminhe|encaminhar)\b", re.IGNORECASE
)
_QUOTED_NAME_PATTERN = re.compile(
    r"\bpara\s+(?:o\s+contato\s+)?[\"“](?P<name>[^\"”]{2,100})[\"”]",
    re.IGNORECASE,
)
_UNQUOTED_NAME_PATTERN = re.compile(
    r"\bpara\s+(?P<name>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'’-]*"
    r"(?:\s+(?:da|das|de|do|dos|e\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'’-]*){0,3})"
    r"(?=\s*(?:[,.;!?]|$|\b(?:por\s+e-?mail|no\s+e-?mail|com\s+o\s+endereço)\b))"
)


@dataclass(frozen=True, slots=True)
class ProtectedInput:
    """Texto sanitizado e dados de destinatário que não devem chegar ao LLM."""

    sanitized_text: str
    recipient_selectors: dict[str, dict[str, str]]
    send_intent_requested: bool


def merge_recipient_selectors(
    existing: dict[str, dict[str, str]],
    incoming: dict[str, dict[str, str]],
    text: str,
) -> tuple[dict[str, dict[str, str]], str]:
    """Merge refs without allowing a clarification to overwrite an older value."""
    merged = dict(existing)
    sanitized = text
    for reference, selector in incoming.items():
        matching_reference = next(
            (
                saved_reference
                for saved_reference, saved_selector in merged.items()
                if saved_selector.get("kind") == selector.get("kind")
                and saved_selector.get("value", "").strip().casefold()
                == selector.get("value", "").strip().casefold()
            ),
            None,
        )
        if matching_reference is None:
            numbers = [
                int(match.group(1))
                for saved_reference in merged
                if (match := re.fullmatch(r"\[DESTINATARIO_(\d+)\]", saved_reference))
            ]
            matching_reference = f"[DESTINATARIO_{max(numbers, default=0) + 1}]"
            merged[matching_reference] = selector
        sanitized = sanitized.replace(reference, matching_reference)
    return merged, sanitized


def protect_user_input(text: str) -> ProtectedInput:
    """Substitui destinatários explícitos por referências e remove outra PII."""
    candidates: list[tuple[int, int, str, str]] = []
    for match in EMAIL_PATTERN.finditer(text):
        prefix_start = max(0, match.start() - 32)
        prefix = text[prefix_start : match.start()]
        destination = re.search(
            r"\b(?:para|a)\s+(?P<label>(?:[\wÀ-ÿ'’-]+\s+){0,4})(?:<)?$",
            prefix,
            re.IGNORECASE,
        )
        if destination:
            start = prefix_start + destination.start("label")
            end = match.end() + int(text[match.end() : match.end() + 1] == ">")
            candidates.append((start, end, "email", match.group()))

    for pattern in (_QUOTED_NAME_PATTERN, _UNQUOTED_NAME_PATTERN):
        for match in pattern.finditer(text):
            name = match.group("name").strip()
            if name:
                candidates.append((match.start("name"), match.end("name"), "name", name))

    candidates.sort(key=lambda item: (item[0], item[1]))
    selected: list[tuple[int, int, str, str]] = []
    for candidate in candidates:
        if not selected or candidate[0] >= selected[-1][1]:
            selected.append(candidate)

    selectors: dict[str, dict[str, str]] = {}
    ref_by_selector: dict[tuple[str, str], str] = {}
    for _, _, kind, value in selected:
        key = (kind, value.strip().casefold())
        reference = ref_by_selector.get(key)
        if reference is None:
            reference = f"[DESTINATARIO_{len(ref_by_selector) + 1}]"
            ref_by_selector[key] = reference
            selectors[reference] = {"kind": kind, "value": value.strip()}

    sanitized = text
    for start, end, kind, value in reversed(selected):
        reference = ref_by_selector[(kind, value.strip().casefold())]
        sanitized = sanitized[:start] + reference + sanitized[end:]

    sanitized = minimize_sensitive_memory_input(sanitized)
    send_requested = _has_positive_send_intent(text)
    return ProtectedInput(sanitized, selectors, send_requested)


def _has_positive_send_intent(text: str) -> bool:
    for match in _SEND_VERB_PATTERN.finditer(text):
        prefix = text[max(0, match.start() - 80) : match.start()]
        clause = re.split(r"[,.;!?]|\bmas\b", prefix, flags=re.IGNORECASE)[-1]
        if not re.search(r"\b(?:não|nao|nunca|sem)\b", clause, re.IGNORECASE):
            return True
    return False


def minimize_pii(text: str) -> str:
    """Remove e-mail, CPF, CNPJ e telefone antes do conteúdo seguir adiante."""
    return PHONE_PATTERN.sub(
        "[TELEFONE]",
        CNPJ_PATTERN.sub("[CNPJ]", CPF_PATTERN.sub("[CPF]", EMAIL_PATTERN.sub("[EMAIL]", text))),
    )


def minimize_sensitive_memory_input(text: str) -> str:
    """Remove PII e valores com aparência de credencial antes de extrair perfil."""
    return SECRET_VALUE_PATTERN.sub(r"\1[REDACTED]", minimize_pii(text))
