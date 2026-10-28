"""Produção autorizada e assinada de relatórios de lote."""

import base64
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from argos_mcp_data.adapters.report_signer import Ed25519LotReportSigner
from argos_mcp_data.application import AccessPolicy, CattleQueryService
from argos_mcp_data.domain.access import AccessContext
from argos_mcp_data.domain.cattle import (
    ApplicationRecord,
    ExceptionAnimalRecord,
    GetLotReportSourceRequest,
    LotRecord,
    Page,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.security.access_directory import AccessDirectory, UserPropertyAccess


class _AccessDirectory(AccessDirectory):
    def __init__(self, property_id, user_id):
        self.property_id = property_id
        self.user_id = user_id

    async def get_user_property_access(self, firebase_uid):
        del firebase_uid
        return UserPropertyAccess(user_id=self.user_id, property_ids={self.property_id})


class _Repository:
    def __init__(self, lot, applications=(), exceptions=(), *, applications_have_more=False):
        self.lot = lot
        self.applications = applications
        self.exceptions = exceptions
        self.applications_have_more = applications_have_more
        self.scopes = []

    async def list_lots(self, scope, request):
        self.scopes.append(scope)
        items = (self.lot,) if self.lot.lot_id == request.lot_id else ()
        return Page[LotRecord](items=items, page_size=1, has_more=False)

    async def list_applications(self, scope, request):
        self.scopes.append(scope)
        return Page[ApplicationRecord](
            items=self.applications,
            page_size=100,
            has_more=self.applications_have_more,
        )

    async def list_exception_animals(self, scope, request):
        self.scopes.append(scope)
        return Page[ExceptionAnimalRecord](
            items=self.exceptions,
            page_size=100,
            has_more=False,
        )


def _setup(*, applications_have_more=False):
    user_id, property_id, lot_id, herd_id = (uuid4() for _ in range(4))
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    private_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signer = Ed25519LotReportSigner(
        key_id="report-test-key",
        private_key_base64=base64.b64encode(private_der).decode("ascii"),
        ttl_seconds=120,
    )
    context = AccessContext(
        firebase_uid="firebase-user-123",
        authorized_property_ids=(property_id,),
        operations=frozenset({"report.read"}),
        agent_id="fazenda",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
        token_id="token-test",
        request_id="request-test",
    )
    lot = LotRecord(
        lot_id=lot_id,
        property_id=property_id,
        property_name="Fazenda A",
        herd_id=herd_id,
        herd_name="Rebanho A",
        herd_breed="Nelore",
        lot_code="L-1",
        category="ENGORDA",
        is_exception_lot=False,
        exception_reason=None,
        head_count=10,
        slaughter_release_date=None,
        fit_for_slaughter=False,
        open_date=date(2026, 1, 1),
        close_date=None,
        active=True,
    )
    application = ApplicationRecord(
        application_id=uuid4(),
        property_id=property_id,
        property_name="Fazenda A",
        lot_id=lot_id,
        lot_code="L-1",
        application_kind="COLETIVA",
        medicine_id=uuid4(),
        medicine_name="Medicamento A",
        active_ingredient="Ativo A",
        medicine_batch_id=uuid4(),
        medicine_batch_number="B-1",
        application_date=date(2026, 2, 1),
        application_time=None,
        dose=1,
        unit="ml",
        route="ORAL",
        head_count_applied=10,
        meat_withdrawal_end=None,
        goal=None,
        active=True,
    )
    repository = _Repository(
        lot,
        (application,),
        applications_have_more=applications_have_more,
    )
    service = CattleQueryService(
        AccessPolicy(_AccessDirectory(property_id, user_id)),
        repository,
        object(),
        report_signer=signer,
    )
    return service, repository, context, private_key.public_key(), user_id, property_id, lot_id


@pytest.mark.asyncio
async def test_report_source_is_complete_scoped_and_signed():
    service, repository, context, public_key, user_id, property_id, lot_id = _setup()

    envelope = await service.get_lot_report_source(
        context,
        GetLotReportSourceRequest(lot_id=lot_id, property_id=property_id),
    )

    canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode()
    public_key.verify(base64.b64decode(envelope.signature), canonical)
    assert envelope.principal_user_id == user_id
    assert envelope.authorized_property_ids == (property_id,)
    assert envelope.report.requested_lot.herd_breed == "Nelore"
    assert envelope.report.applications[0].application_id
    assert all(scope.property_ids == (property_id,) for scope in repository.scopes)


@pytest.mark.asyncio
async def test_report_source_refuses_to_sign_truncated_applications():
    service, _, context, _, _, property_id, lot_id = _setup(applications_have_more=True)

    with pytest.raises(RuntimeError, match="limite completo"):
        await service.get_lot_report_source(
            context,
            GetLotReportSourceRequest(lot_id=lot_id, property_id=property_id),
        )
