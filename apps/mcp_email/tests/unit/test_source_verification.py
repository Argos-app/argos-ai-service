import base64
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from argos_mcp_email.adapters.source_verification import (
    SignedPayloadVerifier,
    TrustedEd25519KeyVerifier,
)
from argos_mcp_email.domain import SourceVerificationError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts import LotReportData, LotReportEnvelope, LotReportIdentity


def envelope() -> LotReportEnvelope:
    report = LotReportData(
        requested_lot=LotReportIdentity(
            lot_id=UUID("00000000-0000-4000-8000-000000000002"),
            lot_code="BOI-FELIZ",
            lot_category="ENGORDA",
            lot_active=True,
            lot_is_exception=False,
            lot_head_count=20,
            lot_open_date=date(2026, 1, 1),
            fit_for_slaughter=True,
            herd_id=uuid4(),
            herd_name="Rebanho Feliz",
            herd_breed="Nelore",
            property_id=UUID("00000000-0000-4000-8000-000000000003"),
            property_name="Fazenda Argos",
        ),
        queried_at=datetime(2026, 10, 3, tzinfo=UTC),
    )
    value = LotReportEnvelope(
        report=report,
        principal_user_id=uuid4(),
        authorized_property_ids=(report.requested_lot.property_id,),
        valid_until=datetime(2026, 10, 10, tzinfo=UTC),
        payload_hash="pending",
        signature="valid-signature",
        signature_key_id="key-1",
    )
    payload = SignedPayloadVerifier._canonical_payload(value)
    return value.model_copy(update={"payload_hash": f"sha256:{sha256(payload).hexdigest()}"})


@pytest.mark.asyncio
async def test_verifies_hash_signature_and_authorized_property() -> None:
    result = await SignedPayloadVerifier(
        lambda payload, signature, key_id: signature == "valid-signature" and key_id == "key-1"
    ).verify(envelope())

    assert result.report.requested_lot.lot_code == "BOI-FELIZ"


@pytest.mark.asyncio
async def test_rejects_payload_with_wrong_hash() -> None:
    value = envelope().model_copy(update={"payload_hash": "sha256:wrong"})

    with pytest.raises(SourceVerificationError):
        await SignedPayloadVerifier(lambda *_: True).verify(value)


@pytest.mark.asyncio
async def test_rejects_expired_payload() -> None:
    value = envelope().model_copy(update={"valid_until": datetime(2026, 10, 1, tzinfo=UTC)})

    with pytest.raises(SourceVerificationError, match="expirado"):
        await SignedPayloadVerifier(
            lambda *_: True,
            clock=lambda: datetime(2026, 10, 5, tzinfo=UTC),
        ).verify(value)


@pytest.mark.asyncio
async def test_rejects_changed_requester_even_with_original_signature() -> None:
    original = envelope()
    changed = original.model_copy(update={"principal_user_id": uuid4()})

    with pytest.raises(SourceVerificationError, match="hash"):
        await SignedPayloadVerifier(lambda *_: True).verify(changed)


@pytest.mark.asyncio
async def test_ed25519_verifier_uses_only_trusted_key_ids() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    value = envelope().model_copy(update={"signature": "pending"})
    payload = SignedPayloadVerifier._canonical_payload(value)
    signed = value.model_copy(
        update={
            "signature": base64.b64encode(private_key.sign(payload)).decode(),
            "payload_hash": f"sha256:{sha256(payload).hexdigest()}",
        }
    )

    checker = TrustedEd25519KeyVerifier({"key-1": public_key})
    result = await SignedPayloadVerifier(checker).verify(signed)

    assert result == signed
    assert not TrustedEd25519KeyVerifier({"other-key": public_key})(
        payload, signed.signature, "key-1"
    )
