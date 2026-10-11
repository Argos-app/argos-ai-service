"""Preparação de relatório com contexto confiável e resposta minimizada ao agente."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from app.application.email_workflow import EmailWorkflow
from app.domain.report_artifacts import ReportArtifact

from packages.contracts import (
    LotReportData,
    LotReportEnvelope,
    LotReportIdentity,
    PreparedReport,
    PreparedReportStatus,
    RecipientRef,
)


class _Artifacts:
    def __init__(self, artifact):
        self.artifact = artifact
        self.scope = None

    async def get(self, reference, **scope):
        self.scope = (reference, scope)
        if reference != self.artifact.reference:
            raise LookupError("not found")
        return self.artifact


class _Preparations:
    def __init__(self):
        self.records = {}

    async def find_matching(self, **key):
        identity = (
            key["principal_user_id"],
            key["session_id"],
            key["report_artifact_ref"],
            key["request_fingerprint"],
        )
        return self.records.get(identity)

    async def save(self, preparation):
        key = (
            preparation.principal_user_id,
            preparation.session_id,
            preparation.report_artifact_ref,
            preparation.request_fingerprint,
        )
        self.records[key] = preparation


class _EmailClient:
    service_name = "email"

    def __init__(self, prepared):
        self.prepared = prepared
        self.calls = []

    async def call_tool(self, name, arguments, *, request_headers=None):
        self.calls.append((name, arguments, request_headers))
        return SimpleNamespace(structuredContent=self.prepared.model_dump(mode="json"), content=[])


def _artifact():
    user_id, property_id, lot_id, session_id, interaction_id = (uuid4() for _ in range(5))
    report = LotReportData(
        requested_lot=LotReportIdentity(
            lot_id=lot_id,
            lot_code="L-1",
            lot_category="ENGORDA",
            lot_active=True,
            lot_is_exception=False,
            lot_head_count=3,
            lot_open_date=date(2026, 1, 1),
            fit_for_slaughter=False,
            herd_id=uuid4(),
            herd_name="Rebanho",
            herd_breed="Nelore",
            property_id=property_id,
            property_name="Fazenda",
        ),
        queried_at=datetime.now(UTC),
    )
    envelope = LotReportEnvelope(
        report=report,
        principal_user_id=user_id,
        authorized_property_ids=(property_id,),
        valid_until=datetime.now(UTC) + timedelta(minutes=15),
        payload_hash="pending",
        signature="test-signature",
        signature_key_id="test-key",
    )
    canonical = envelope.model_dump_json(exclude={"payload_hash", "signature"}).encode()
    envelope = envelope.model_copy(
        update={"payload_hash": f"sha256:{sha256(canonical).hexdigest()}"}
    )
    artifact = ReportArtifact(
        reference=uuid4(),
        envelope=envelope,
        principal_user_id=user_id,
        session_id=session_id,
        interaction_id=interaction_id,
        task_id="farm-task",
        created_at=datetime.now(UTC),
    )
    return artifact, property_id


def test_prepare_report_uses_protected_context_persists_full_response_and_reuses_it():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Sensitive generated subject",
        body="Sensitive generated report body",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    email_client = _EmailClient(prepared)
    preparations = _Preparations()
    artifacts = _Artifacts(artifact)
    workflow = EmailWorkflow(
        email_client=email_client,
        report_artifacts=artifacts,
        preparations=preparations,
    )
    context = {
        "argos_user_id": str(artifact.principal_user_id),
        "session_id": str(artifact.session_id),
        "interaction_id": str(uuid4()),
        "argos_authorized_property_ids": (str(property_id),),
        "argos_report_artifact_scopes": {
            str(artifact.reference): {
                "interaction_id": str(artifact.interaction_id),
                "task_id": artifact.task_id,
            }
        },
        "recipient_selectors": {"[DESTINATARIO_1]": {"kind": "name", "value": "Ana"}},
    }

    async def prepare_twice():
        first = await workflow.prepare(
            report_artifact_ref=artifact.reference,
            report_title="Relatório do lote L-1",
            configurable=context,
        )
        second = await workflow.prepare(
            report_artifact_ref=artifact.reference,
            report_title="Relatório do lote L-1",
            configurable=context,
        )
        return json.loads(first), json.loads(second)

    result, repeated = asyncio.run(prepare_twice())

    assert result["status"] == "prepared"
    assert result["report_id"] == str(prepared.report_id)
    assert repeated["reused"] is True
    assert len(email_client.calls) == 1
    sent = email_client.calls[0][1]["payload"]
    assert sent["requested_by"] == str(artifact.principal_user_id)
    assert sent["report_source"]["payload_hash"] == artifact.envelope.payload_hash
    assert sent["recipients"] == [{"kind": "name", "value": "Ana"}]
    assert sent["report_title"] == "Relatório do lote L-1"
    assert "ana@example.com" not in json.dumps(result)
    assert "Sensitive generated report body" not in json.dumps(result)
    assert next(iter(preparations.records.values())).prepared_report == prepared
    assert artifacts.scope[1]["task_id"] == artifact.task_id
