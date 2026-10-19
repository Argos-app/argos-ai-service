"""Assinatura Ed25519 de envelopes produzidos pelo MCP de dados."""

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from argos_mcp_data.domain.access import AuthorizedScope
from argos_mcp_data.ports.report_signer import LotReportSigner
from packages.contracts import LotReportData, LotReportEnvelope


class Ed25519LotReportSigner(LotReportSigner):
    def __init__(self, *, key_id: str, private_key_base64: str, ttl_seconds: int = 300) -> None:
        if not key_id.strip():
            raise ValueError("MCP_DATA_REPORT_SIGNING_KEY_ID não pode estar vazio")
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("validade do envelope deve estar entre 1 e 300 segundos")
        try:
            der = base64.b64decode(private_key_base64, validate=True)
            key = serialization.load_der_private_key(der, password=None)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "MCP_DATA_REPORT_SIGNING_PRIVATE_KEY deve ser PKCS#8 DER Ed25519 em Base64"
            ) from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("a chave de assinatura de relatório deve ser Ed25519")
        self._key_id = key_id
        self._private_key = key
        self._ttl_seconds = ttl_seconds

    def sign(self, report: LotReportData, scope: AuthorizedScope) -> LotReportEnvelope:
        envelope = LotReportEnvelope(
            report=report,
            principal_user_id=scope.user_id,
            authorized_property_ids=scope.property_ids,
            valid_until=datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
            payload_hash="pending",
            signature="pending",
            signature_key_id=self._key_id,
        )
        canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode("utf-8")
        payload_hash = f"sha256:{sha256(canonical).hexdigest()}"
        signature = base64.b64encode(self._private_key.sign(canonical)).decode("ascii")
        return envelope.model_copy(update={"payload_hash": payload_hash, "signature": signature})
