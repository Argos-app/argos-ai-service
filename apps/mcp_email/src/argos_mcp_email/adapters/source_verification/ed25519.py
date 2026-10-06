"""Verificação Ed25519 usando somente chaves públicas confiáveis."""

import base64
import binascii
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class TrustedEd25519KeyVerifier:
    """Verifica assinaturas Ed25519 identificadas por ``signature_key_id``.

    As chaves podem ser fornecidas como 32 bytes ou como Base64. Chaves que
    não estejam no mapa confiável são rejeitadas antes da verificação.
    """

    def __init__(self, trusted_keys: Mapping[str, bytes | str]) -> None:
        self._keys = {
            key_id: self._load_key(public_key) for key_id, public_key in trusted_keys.items()
        }

    def __call__(self, payload: bytes, signature: str, key_id: str) -> bool:
        public_key = self._keys.get(key_id)
        if public_key is None:
            return False
        try:
            public_key.verify(self._decode(signature), payload)
        except InvalidSignature, ValueError, binascii.Error:
            return False
        return True

    @staticmethod
    def _load_key(public_key: bytes | str) -> Ed25519PublicKey:
        try:
            value = (
                base64.b64decode(public_key, validate=True)
                if isinstance(public_key, str)
                else public_key
            )
        except binascii.Error as exc:
            raise ValueError("chave pública Ed25519 deve ser Base64 válido") from exc
        if len(value) != 32:
            raise ValueError("chave pública Ed25519 deve ter 32 bytes")
        return Ed25519PublicKey.from_public_bytes(value)

    @staticmethod
    def _decode(signature: str) -> bytes:
        value = base64.b64decode(signature, validate=True)
        if len(value) != 64:
            raise ValueError("assinatura Ed25519 deve ter 64 bytes")
        return value
