"""Funções compartilhadas pelos fluxos de preparação e envio de e-mail."""

import json
from hashlib import sha256
from typing import Any

from packages.contracts import RecipientRef


def _payload(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    text_blocks = [
        block.text
        for block in getattr(result, "content", ())
        if isinstance(getattr(block, "text", None), str)
    ]
    if len(text_blocks) != 1:
        raise ValueError("resposta de preparação ausente ou ambígua")
    try:
        return json.loads(text_blocks[0])
    except json.JSONDecodeError as exc:
        raise ValueError("resposta de preparação não contém JSON válido") from exc


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _recipient_selectors_match(
    selectors: dict[str, Any], recipients: tuple[RecipientRef, ...]
) -> bool:
    def normalize(value: str) -> str:
        apostrophes = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})
        return value.strip().translate(apostrophes).casefold()

    if len(selectors) != len(recipients):
        return False
    unmatched = set(range(len(recipients)))
    for selector in selectors.values():
        if not isinstance(selector, dict):
            return False
        kind = selector.get("kind")
        value = selector.get("value")
        if kind not in {"name", "email"} or not isinstance(value, str):
            return False
        normalized = normalize(value)
        matches = [
            index
            for index in unmatched
            if normalized
            == normalize(recipients[index].name if kind == "name" else recipients[index].email)
        ]
        if len(matches) != 1:
            return False
        unmatched.remove(matches[0])
    return not unmatched
