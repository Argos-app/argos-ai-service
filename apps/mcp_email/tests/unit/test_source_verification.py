from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from argos_mcp_email.adapters.source_verification import SignedPayloadVerifier
from argos_mcp_email.domain.errors import SourceVerificationError

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
    payload = report.model_dump_json().encode("utf-8")
    return LotReportEnvelope(
        report=report,
        principal_user_id=uuid4(),
        authorized_property_ids=(report.requested_lot.property_id,),
        payload_hash=f"sha256:{sha256(payload).hexdigest()}",
        signature="valid-signature",
        signature_key_id="key-1",
    )


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
