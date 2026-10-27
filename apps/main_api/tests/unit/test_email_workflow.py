"""Preparação de relatório com contexto confiável e resposta minimizada ao agente."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.agent_schemas.synthesis import EmailDispatchEvidence
from app.application.email_dispatch_workflow import (
    EmailDispatchWorkflow,
    _recipient_selectors_match,
    _safe_result,
)
from app.application.email_workflow import EmailWorkflow, _fingerprint
from app.domain.email_preparations import (
    EmailOperationReference,
    EmailPreparation,
    EmailSendAuthorization,
    SendAuthorizationOrigin,
)
from app.domain.report_artifacts import ReportArtifact
from app.infrastructure.persistence.email_preparations import (
    _operation_reference,
    _operation_reference_document,
)
from app.ports.email_preparation_repository import EmailSendAuthorizationConflictError

from packages.contracts import (
    DispatchStatus,
    LotReportData,
    LotReportEnvelope,
    LotReportIdentity,
    PreparedReport,
    PreparedReportStatus,
    RecipientRef,
    RecipientResolution,
    RecipientResolutionStatus,
    RecipientSelector,
    ReportStatusResult,
    SendReportResult,
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

    async def get_owned(self, *, reference, principal_user_id, session_id):
        if (
            reference != self.artifact.reference
            or principal_user_id != self.artifact.principal_user_id
            or session_id != self.artifact.session_id
        ):
            raise LookupError("not found")
        return self.artifact


class _Preparations:
    def __init__(self):
        self.records = {}
        self.pending = []
        self.authorizations = {}
        self.operation_references = {}

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

    async def get_by_reference(self, *, reference, principal_user_id, session_id):
        return next(
            (
                item
                for item in self.records.values()
                if item.reference == reference
                and item.principal_user_id == principal_user_id
                and item.session_id == session_id
            ),
            None,
        )

    async def list_prepared_for_session(self, *, principal_user_id, session_id, limit=20):
        values = [
            item
            for item in self.records.values()
            if item.principal_user_id == principal_user_id
            and item.session_id == session_id
            and item.prepared_report.status == PreparedReportStatus.PREPARED
        ]
        return tuple(values[:limit])

    async def save_pending_recipient_resolution(self, resolution):
        self.pending.append(resolution)

    async def get_pending_recipient_resolution(self, **scope):
        return next(
            (
                item
                for item in reversed(self.pending)
                if item.principal_user_id == scope["principal_user_id"]
                and item.session_id == scope["session_id"]
                and item.interaction_id == scope["interaction_id"]
                and (scope.get("question_id") is None or item.question_id == scope["question_id"])
            ),
            None,
        )

    async def reserve_send_authorization(self, authorization):
        key = (
            authorization.principal_user_id,
            authorization.session_id,
            authorization.source_interaction_id,
        )
        existing = self.authorizations.get(key)
        if existing is not None:
            fields = (
                "preparation_reference",
                "report_id",
                "request_fingerprint",
                "prepared_fingerprint",
                "recipients_fingerprint",
                "origin",
            )
            if any(getattr(existing, field) != getattr(authorization, field) for field in fields):
                raise EmailSendAuthorizationConflictError("authorization conflict")
            return existing
        self.authorizations[key] = authorization
        return authorization

    async def confirm_send_authorization(
        self,
        *,
        principal_user_id,
        session_id,
        source_interaction_id,
        question_id,
        question_expires_at,
        now,
    ):
        key = (principal_user_id, session_id, source_interaction_id)
        authorization = self.authorizations.get(key)
        if authorization is None:
            return None
        if authorization.origin == SendAuthorizationOrigin.CONFIRMED_QUESTION:
            if authorization.question_id == question_id:
                return authorization
            raise EmailSendAuthorizationConflictError("different confirmation")
        confirmed = replace(
            authorization,
            origin=SendAuthorizationOrigin.CONFIRMED_QUESTION,
            authorized_at=now,
            expires_at=min(authorization.expires_at, question_expires_at),
            question_id=question_id,
            question_expires_at=question_expires_at,
        )
        self.authorizations[key] = confirmed
        return confirmed

    async def get_send_authorization(self, **scope):
        return self.authorizations.get(
            (
                scope["principal_user_id"],
                scope["session_id"],
                scope["source_interaction_id"],
            )
        )

    async def list_send_authorizations(self, *, principal_user_id, session_id, limit=20):
        values = [
            item
            for item in self.authorizations.values()
            if item.principal_user_id == principal_user_id and item.session_id == session_id
        ]
        return tuple(values[:limit])

    async def get_send_authorization_by_id(
        self, *, authorization_id, principal_user_id, session_id
    ):
        return next(
            (
                item
                for item in self.authorizations.values()
                if item.authorization_id == authorization_id
                and item.principal_user_id == principal_user_id
                and item.session_id == session_id
            ),
            None,
        )

    async def get_send_operation_reference(
        self, *, authorization_id, principal_user_id, session_id
    ):
        reference = self.operation_references.get(authorization_id)
        if reference is None or (reference.principal_user_id, reference.session_id) != (
            principal_user_id,
            session_id,
        ):
            return None
        return reference

    async def save_send_operation_reference(self, reference):
        existing = self.operation_references.get(reference.authorization_id)
        if existing is not None and existing.operation_id != reference.operation_id:
            raise EmailSendAuthorizationConflictError("operation conflict")
        self.operation_references[reference.authorization_id] = reference
        return reference


class _EmailClient:
    service_name = "email"

    def __init__(self, prepared):
        self.prepared = prepared
        self.calls = []

    async def call_tool(self, name, arguments, *, request_headers=None):
        self.calls.append((name, arguments, request_headers))
        if callable(self.prepared):
            return self.prepared(name, arguments)
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
        expires_at=datetime.now(UTC) + timedelta(hours=1),
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
    assert datetime.fromisoformat(result["expires_at"]) == artifact.envelope.valid_until
    saved = next(iter(preparations.records.values()))
    assert saved.prepared_report.expires_at == artifact.envelope.valid_until
    assert repeated["reused"] is True
    assert len(email_client.calls) == 1
    sent = email_client.calls[0][1]["payload"]
    assert sent["requested_by"] == str(artifact.principal_user_id)
    assert sent["report_source"]["payload_hash"] == artifact.envelope.payload_hash
    assert sent["recipients"] == [{"kind": "name", "value": "Ana"}]
    assert sent["report_title"] == "Relatório do lote L-1"
    assert "ana@example.com" not in json.dumps(result)
    assert "Sensitive generated report body" not in json.dumps(result)
    assert saved.prepared_report == prepared.model_copy(
        update={"expires_at": artifact.envelope.valid_until}
    )
    assert preparations.authorizations == {}
    assert artifacts.scope[1]["task_id"] == artifact.task_id


def test_explicit_send_reserves_one_authorization_and_stable_idempotency_key():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório do lote L-1",
        body="Resumo do lote.",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    email_client = _EmailClient(prepared)
    preparations = _Preparations()
    workflow = EmailWorkflow(
        email_client=email_client,
        report_artifacts=_Artifacts(artifact),
        preparations=preparations,
    )
    source_interaction_id = uuid4()
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
        "argos_send_intent_requested": True,
        "argos_send_intent_source_interaction_id": str(source_interaction_id),
    }

    async def prepare_twice():
        for _ in range(2):
            await workflow.prepare(
                report_artifact_ref=artifact.reference,
                report_title="Relatório do lote L-1",
                configurable=context,
            )

    asyncio.run(prepare_twice())

    assert len(preparations.authorizations) == 1
    authorization = next(iter(preparations.authorizations.values()))
    assert authorization.source_interaction_id == source_interaction_id
    assert authorization.report_id == prepared.report_id
    assert authorization.origin == "explicit_request"
    assert authorization.idempotency_key
    original_key = authorization.idempotency_key

    email_client.prepared = prepared.model_copy(
        update={"report_id": uuid4(), "body": "Conteúdo alterado."}
    )

    async def prepare_changed_report():
        await workflow.prepare(
            report_artifact_ref=artifact.reference,
            report_title="Relatório atualizado",
            configurable=context,
        )

    with pytest.raises(ValueError, match="já está vinculado a outra preparação"):
        asyncio.run(prepare_changed_report())
    assert next(iter(preparations.authorizations.values())).idempotency_key == original_key


def test_ambiguous_recipient_question_displays_full_emails_only_in_choice_labels():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.NEEDS_RECIPIENTS,
        requested_by=artifact.principal_user_id,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        unresolved=(
            RecipientResolution(
                selector=RecipientSelector(kind="name", value="Alex"),
                status=RecipientResolutionStatus.AMBIGUOUS,
                matches=(
                    RecipientRef(
                        recipient_id=uuid4(), name="Alex Silva", email="alex1@example.com"
                    ),
                    RecipientRef(
                        recipient_id=uuid4(), name="Alex Souza", email="alex2@example.com"
                    ),
                ),
            ),
        ),
    )
    email_client = _EmailClient(prepared)
    preparations = _Preparations()
    workflow = EmailWorkflow(
        email_client=email_client,
        report_artifacts=_Artifacts(artifact),
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
        "recipient_selectors": {"[DESTINATARIO_1]": {"kind": "name", "value": "Alex"}},
        "argos_send_intent_requested": True,
        "argos_send_intent_source_interaction_id": str(uuid4()),
    }

    async def prepare():
        return await workflow.prepare(
            report_artifact_ref=artifact.reference,
            report_title="Relatório do lote L-1",
            configurable=context,
        )

    result_text = asyncio.run(prepare())
    result = json.loads(result_text)
    pending = preparations.pending[0]

    assert result["status"] == "needs_recipients"
    assert result["recipient_question_id"] == str(pending.question_id)
    assert pending.input_type == "single_choice"
    assert len(pending.options) == 2
    assert "alex1@example.com" not in result_text
    assert "alex2@example.com" not in result_text
    assert pending.options[0].selector.value == "alex1@example.com"
    assert "alex1@example.com" in pending.options[0].label
    assert "alex2@example.com" in pending.options[1].label
    assert preparations.authorizations == {}


def _authorized_dispatch(artifact, prepared, property_id, client):
    now = datetime.now(UTC)
    question_id = uuid4()
    question_expires_at = now + timedelta(minutes=5)
    preparation = EmailPreparation(
        reference=uuid4(),
        principal_user_id=artifact.principal_user_id,
        session_id=artifact.session_id,
        interaction_id=uuid4(),
        report_artifact_ref=artifact.reference,
        request_fingerprint="request-hash",
        prepared_report=prepared,
        created_at=now,
    )
    authorization = EmailSendAuthorization(
        authorization_id=uuid4(),
        principal_user_id=artifact.principal_user_id,
        session_id=artifact.session_id,
        source_interaction_id=uuid4(),
        preparation_reference=preparation.reference,
        report_id=prepared.report_id,
        request_fingerprint=preparation.request_fingerprint,
        prepared_fingerprint=_fingerprint(prepared.model_dump(mode="json")),
        recipients_fingerprint=_fingerprint(
            [recipient.model_dump(mode="json") for recipient in prepared.recipients]
        ),
        origin=SendAuthorizationOrigin.CONFIRMED_QUESTION,
        idempotency_key=str(uuid4()),
        authorized_at=now,
        expires_at=min(prepared.expires_at, question_expires_at),
        question_id=question_id,
        question_expires_at=question_expires_at,
    )
    repository = _Preparations()
    repository.records[
        (
            preparation.principal_user_id,
            preparation.session_id,
            preparation.report_artifact_ref,
            preparation.request_fingerprint,
        )
    ] = preparation
    repository.authorizations[
        (
            authorization.principal_user_id,
            authorization.session_id,
            authorization.source_interaction_id,
        )
    ] = authorization
    workflow = EmailDispatchWorkflow(
        email_client=client,
        preparations=repository,
        report_artifacts=_Artifacts(artifact),
    )
    config = {
        "argos_user_id": str(artifact.principal_user_id),
        "session_id": str(artifact.session_id),
        "argos_send_intent_source_interaction_id": str(authorization.source_interaction_id),
        "argos_authorized_property_ids": (str(property_id),),
        "argos_confirmed_send_question_id": str(question_id),
        "argos_confirmed_send_report_id": str(prepared.report_id),
        "argos_confirmed_send_question_expires_at": question_expires_at.isoformat(),
    }
    return workflow, repository, authorization, config


def _dispatch_result(report_id, operation_id=None, status=DispatchStatus.ACCEPTED):
    now = datetime.now(UTC)
    return SendReportResult(
        operation_id=operation_id or uuid4(),
        report_id=report_id,
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_dispatch_result_carries_structured_status_and_recipient_count():
    report_id = uuid4()
    result = _dispatch_result(report_id)

    payload = json.loads(_safe_result(result, recipient_count=5))

    assert payload["status"] == "accepted"
    assert payload["confirmed"] is True
    assert payload["report_id"] == str(report_id)
    assert payload["operation_id"] == str(result.operation_id)
    assert payload["recipient_count"] == 5
    assert "não confirma a entrega" in payload["message"]


def test_recovered_dispatch_result_matches_graph_evidence_contract():
    result = _dispatch_result(uuid4())

    evidence = EmailDispatchEvidence.model_validate_json(
        _safe_result(result, recovered=True, status_refresh_failed=True)
    )

    assert evidence.status == "accepted"
    assert evidence.confirmed is True
    assert evidence.recovered is True
    assert "último estado confirmado" in evidence.message


def test_dispatch_persists_reference_and_status_uses_stored_operation_id():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    sent = _dispatch_result(prepared.report_id)

    def respond(name, arguments):
        if name == "send_report":
            return SimpleNamespace(structuredContent=sent.model_dump(mode="json"), content=[])
        requested_operation = arguments["payload"]["operation_id"]
        assert requested_operation == str(sent.operation_id)
        status = ReportStatusResult.model_validate(sent.model_dump(mode="json"))
        return SimpleNamespace(structuredContent=status.model_dump(mode="json"), content=[])

    client = _EmailClient(respond)
    workflow, repository, authorization, config = _authorized_dispatch(
        artifact, prepared, property_id, client
    )

    async def execute():
        send_result = json.loads(await workflow.send(configurable=config))
        status_result = json.loads(
            await workflow.status(
                authorization_id=authorization.authorization_id,
                configurable=config,
            )
        )
        return send_result, status_result

    send_result, status_result = asyncio.run(execute())

    assert send_result["status"] == "accepted"
    assert status_result["status"] == "accepted"
    assert len(client.calls) == 2
    assert [item[0] for item in client.calls] == ["send_report", "get_report_status"]
    assert (
        repository.operation_references[authorization.authorization_id].operation_id
        == sent.operation_id
    )
    assert send_result["operation_id"] == str(sent.operation_id)
    assert "idempotency_key" not in json.dumps(send_result)


def test_confirmation_matches_unicode_apostrophe_variant_of_persisted_recipient():
    recipient = RecipientRef(
        recipient_id=uuid4(), name="Vinicius D'Oliveira", email="vinicius@example.com"
    )

    assert _recipient_selectors_match(
        {"[DESTINATARIO_1]": {"kind": "name", "value": "Vinicius D’Oliveira"}},
        (recipient,),
    )


def test_explicit_confirmation_sends_the_unique_prepared_report_without_repreparing():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    accepted = _dispatch_result(prepared.report_id)

    def respond(name, arguments):
        assert name == "send_report"
        assert arguments["payload"]["report_id"] == str(prepared.report_id)
        return SimpleNamespace(structuredContent=accepted.model_dump(mode="json"), content=[])

    client = _EmailClient(respond)
    workflow, repository, original_authorization, config = _authorized_dispatch(
        artifact, prepared, property_id, client
    )
    repository.authorizations.clear()
    config.update(
        {
            "argos_send_intent_requested": True,
            "recipient_selectors": {"recipient-1": {"kind": "email", "value": "ana@example.com"}},
        }
    )

    result = json.loads(asyncio.run(workflow.send(configurable=config)))

    assert result["status"] == "accepted"
    assert result["report_id"] == str(prepared.report_id)
    assert [name for name, _, _ in client.calls] == ["send_report"]
    new_authorization = next(iter(repository.authorizations.values()))
    assert new_authorization.report_id == prepared.report_id
    assert new_authorization.preparation_reference == original_authorization.preparation_reference


def test_explicit_confirmation_rejects_ambiguous_prepared_reports():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    client = _EmailClient(lambda *_: pytest.fail("não deve enviar com preparação ambígua"))
    workflow, repository, _, config = _authorized_dispatch(artifact, prepared, property_id, client)
    repository.authorizations.clear()
    duplicate = next(iter(repository.records.values()))
    duplicate_key = (artifact.principal_user_id, artifact.session_id, uuid4(), "duplicate")
    duplicate_report = prepared.model_copy(update={"report_id": uuid4()})
    repository.records[duplicate_key] = replace(
        duplicate, reference=uuid4(), prepared_report=duplicate_report
    )
    config.update(
        {
            "argos_send_intent_requested": True,
            "argos_confirmed_send_report_id": str(uuid4()),
            "recipient_selectors": {"recipient-1": {"kind": "email", "value": "ana@example.com"}},
        }
    )

    with pytest.raises(ValueError, match="única preparação válida"):
        asyncio.run(workflow.send(configurable=config))
    assert client.calls == []


def test_explicit_send_reservation_dispatches_without_extra_confirmation():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    accepted = _dispatch_result(prepared.report_id)
    client = _EmailClient(accepted)
    workflow, repository, original, config = _authorized_dispatch(
        artifact, prepared, property_id, client
    )
    pending = replace(
        original,
        origin=SendAuthorizationOrigin.EXPLICIT_REQUEST,
        question_id=None,
        question_expires_at=None,
        expires_at=prepared.expires_at,
    )
    repository.authorizations[
        (pending.principal_user_id, pending.session_id, pending.source_interaction_id)
    ] = pending
    config.pop("argos_confirmed_send_question_id")
    config.pop("argos_confirmed_send_question_expires_at")
    config.pop("argos_confirmed_send_report_id")
    config["argos_send_intent_requested"] = True

    sent = json.loads(asyncio.run(workflow.send(configurable=config)))
    reserved = repository.authorizations[
        (pending.principal_user_id, pending.session_id, pending.source_interaction_id)
    ]
    assert sent["status"] == "accepted"
    assert reserved.origin == SendAuthorizationOrigin.EXPLICIT_REQUEST
    assert reserved.question_id is None
    assert [name for name, _, _ in client.calls] == ["send_report"]


def test_recover_interaction_outcome_reads_mcp_without_resending():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    sent = _dispatch_result(prepared.report_id)

    def respond(name, arguments):
        assert name == "get_report_status"
        assert arguments["payload"]["operation_id"] == str(sent.operation_id)
        return SimpleNamespace(
            structuredContent=ReportStatusResult.model_validate(
                sent.model_dump(mode="json")
            ).model_dump(mode="json"),
            content=[],
        )

    client = _EmailClient(respond)
    workflow, repository, authorization, config = _authorized_dispatch(
        artifact, prepared, property_id, client
    )
    repository.operation_references[authorization.authorization_id] = EmailOperationReference(
        authorization_id=authorization.authorization_id,
        principal_user_id=artifact.principal_user_id,
        session_id=artifact.session_id,
        operation_id=sent.operation_id,
        recorded_at=datetime.now(UTC),
    )

    summary = asyncio.run(workflow.recover_interaction_outcome(configurable=config))

    recovered = json.loads(summary)
    assert recovered["status"] == "accepted"
    assert recovered["confirmed"] is True
    assert recovered["operation_id"] == str(sent.operation_id)
    assert recovered["recipient_count"] == 1
    assert "não confirma a entrega" in recovered["message"]
    assert [name for name, *_ in client.calls] == ["get_report_status"]


def test_operation_reference_round_trips_last_known_provider_status():
    artifact, _ = _artifact()
    sent = _dispatch_result(uuid4())
    reference = EmailOperationReference(
        authorization_id=uuid4(),
        principal_user_id=artifact.principal_user_id,
        session_id=artifact.session_id,
        operation_id=sent.operation_id,
        recorded_at=datetime.now(UTC),
        last_known_status=sent.status,
        last_known_updated_at=sent.updated_at,
    )

    restored = _operation_reference(_operation_reference_document(reference))

    assert restored == reference


def test_recovery_preserves_last_known_send_result_when_status_lookup_fails():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    sent = _dispatch_result(prepared.report_id)

    def respond(name, _arguments):
        if name == "send_report":
            return SimpleNamespace(structuredContent=sent.model_dump(mode="json"), content=[])
        raise TimeoutError("consulta posterior temporariamente indisponível")

    client = _EmailClient(respond)
    workflow, _, _, config = _authorized_dispatch(artifact, prepared, property_id, client)

    async def send_and_recover():
        sent_result = json.loads(await workflow.send(configurable=config))
        recovered_result = json.loads(
            await workflow.recover_interaction_outcome(configurable=config)
        )
        return sent_result, recovered_result

    sent_result, recovered_result = asyncio.run(send_and_recover())

    assert sent_result["status"] == "accepted"
    assert recovered_result["status"] == "accepted"
    assert recovered_result["confirmed"] is True
    assert "não confirma a entrega" in recovered_result["message"]
    assert "último estado confirmado" in recovered_result["message"]
    assert [name for name, *_ in client.calls] == ["send_report", "get_report_status"]


def test_lost_send_response_retries_with_same_report_and_idempotency_key():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    calls = []
    result = _dispatch_result(prepared.report_id)

    def respond(name, arguments):
        calls.append(arguments["payload"])
        if len(calls) == 1:
            raise TimeoutError("response lost after remote operation")
        return SimpleNamespace(structuredContent=result.model_dump(mode="json"), content=[])

    client = _EmailClient(respond)
    workflow, repository, authorization, config = _authorized_dispatch(
        artifact, prepared, property_id, client
    )

    async def retry():
        first = json.loads(await workflow.send(configurable=config))
        second = json.loads(await workflow.send(configurable=config))
        return first, second

    first, second = asyncio.run(retry())

    assert first["status"] == "unknown"
    assert second["status"] == "accepted"
    assert calls[0] == calls[1]
    assert calls[0]["report_id"] == str(prepared.report_id)
    assert calls[0]["idempotency_key"] == authorization.idempotency_key
    assert (
        repository.operation_references[authorization.authorization_id].operation_id
        == result.operation_id
    )


def test_status_without_action_ref_returns_sanitized_choices_for_own_history():
    artifact, property_id = _artifact()
    prepared = PreparedReport(
        report_id=uuid4(),
        status=PreparedReportStatus.PREPARED,
        requested_by=artifact.principal_user_id,
        recipients=(RecipientRef(recipient_id=uuid4(), name="Ana", email="ana@example.com"),),
        subject="Relatório",
        body="Conteúdo",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    result = _dispatch_result(prepared.report_id)

    def respond(name, arguments):
        assert name == "get_report_status"
        assert arguments["payload"]["requested_by"] == str(artifact.principal_user_id)
        return SimpleNamespace(
            structuredContent=ReportStatusResult.model_validate(
                result.model_dump(mode="json")
            ).model_dump(mode="json"),
            content=[],
        )

    client = _EmailClient(respond)
    workflow, repository, authorization, config = _authorized_dispatch(
        artifact, prepared, property_id, client
    )
    second_authorization = replace(
        authorization,
        authorization_id=uuid4(),
        source_interaction_id=uuid4(),
        idempotency_key=str(uuid4()),
    )
    repository.authorizations[
        (
            second_authorization.principal_user_id,
            second_authorization.session_id,
            second_authorization.source_interaction_id,
        )
    ] = second_authorization
    operation_ids = (uuid4(), uuid4())
    for action, operation_id in zip(
        (authorization, second_authorization), operation_ids, strict=True
    ):
        repository.operation_references[action.authorization_id] = EmailOperationReference(
            authorization_id=action.authorization_id,
            principal_user_id=action.principal_user_id,
            session_id=action.session_id,
            operation_id=operation_id,
            recorded_at=datetime.now(UTC),
        )

    response = json.loads(asyncio.run(workflow.status(authorization_id=None, configurable=config)))

    assert response["status"] == "needs_action_selection"
    assert {item["action_ref"] for item in response["actions"]} == {
        str(authorization.authorization_id),
        str(second_authorization.authorization_id),
    }
    assert all(item["status"] == "accepted" for item in response["actions"])
    assert all(str(operation_id) not in json.dumps(response) for operation_id in operation_ids)
