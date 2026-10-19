"""Verificação de assinaturas Ed25519 emitidas por serviços internos."""

import base64
import binascii
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class TrustedEd25519SignatureVerifier:
    def __init__(self, trusted_keys: Mapping[str, str]) -> None:
        self._keys = {key_id: self._load_key(value) for key_id, value in trusted_keys.items()}

    def __call__(self, payload: bytes, signature: str, key_id: str) -> bool:
        key = self._keys.get(key_id)
        if key is None:
            return False
        try:
            decoded = base64.b64decode(signature, validate=True)
            if len(decoded) != 64:
                return False
            key.verify(decoded, payload)
        except InvalidSignature, ValueError, binascii.Error:
            return False
        return True

    @staticmethod
    def _load_key(value: str) -> Ed25519PublicKey:
        try:
            raw = base64.b64decode(value, validate=True)
        except (TypeError, binascii.Error) as exc:
            raise ValueError("chave pública Ed25519 deve ser Base64 válido") from exc
        if len(raw) != 32:
            raise ValueError("chave pública Ed25519 deve conter 32 bytes")
        return Ed25519PublicKey.from_public_bytes(raw)
