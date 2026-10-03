"""Verifica hash e assinatura do envelope produzido pelo MCP de dados."""

from collections.abc import Callable
from hashlib import sha256

from argos_mcp_email.domain.errors import SourceVerificationError
from packages.contracts import LotReportEnvelope

SignatureChecker = Callable[[bytes, str, str], bool]


class SignedPayloadVerifier:
    """Adapter sem dependência de biblioteca criptográfica específica.

    ``signature_checker`` recebe o payload canônico, a assinatura e o ID da
    chave. A infraestrutura pode injetar Ed25519, KMS ou outro verificador.
    """

    def __init__(
        self,
        signature_checker: SignatureChecker,
        *,
        expected_source_service: str = "mcp_data",
    ) -> None:
        self._signature_checker = signature_checker
        self._expected_source_service = expected_source_service

    async def verify(self, source: LotReportEnvelope) -> LotReportEnvelope:
        if source.source_service != self._expected_source_service:
            raise SourceVerificationError("origem do payload não é o MCP de dados")
        if source.report.requested_lot.property_id not in source.authorized_property_ids:
            raise SourceVerificationError("propriedade do lote não está no escopo autorizado")

        payload = self._canonical_payload(source)
        expected_hash = f"sha256:{sha256(payload).hexdigest()}"
        if source.payload_hash != expected_hash:
            raise SourceVerificationError("hash do payload não corresponde aos dados")
        if not self._signature_checker(payload, source.signature, source.signature_key_id):
            raise SourceVerificationError("assinatura do payload inválida")
        return source

    @staticmethod
    def _canonical_payload(source: LotReportEnvelope) -> bytes:
        """Serializa somente os dados cobertos pela assinatura."""

        return source.report.model_dump_json().encode("utf-8")
