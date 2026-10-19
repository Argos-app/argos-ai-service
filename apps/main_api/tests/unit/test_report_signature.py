"""Verificação criptográfica dos envelopes do MCP de dados."""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.security.signatures import TrustedEd25519SignatureVerifier


def test_trusted_signature_verifier_accepts_only_trusted_unmodified_payloads():
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    payload = b"signed report payload"
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
    verifier = TrustedEd25519SignatureVerifier(
        {"report-key": base64.b64encode(public_key).decode("ascii")}
    )

    assert verifier(payload, signature, "report-key")
    assert not verifier(b"altered payload", signature, "report-key")
    assert not verifier(payload, signature, "unknown-key")
