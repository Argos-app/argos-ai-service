"""Autorização e estados explícitos da barreira de atualização do corpus."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argos_a2a_agent.application import EnsureCurrentCorpus, EnsureCurrentCorpusOutcome
from argos_a2a_agent.domain.errors import CorpusUpdatingError, SourceReadError
from argos_a2a_agent.domain.models import CorpusSyncResult

from packages.contracts.a2a import CorpusRefreshStatus
from packages.security.access import DocumentAccessContext
from packages.security.document_access import DocumentAccessPolicy
from packages.security.errors import AccessContextError, AccessDeniedError


class PermissionDirectory:
    def __init__(self, events: list[str], *, allowed: bool = True) -> None:
        self.events = events
        self.allowed = allowed

    async def has_active_documents_read_permission(self, user_id) -> bool:
        self.events.append("permission")
        return self.allowed


class Synchronizer:
    def __init__(self, events: list[str], result=None, error: Exception | None = None) -> None:
        self.events = events
        self.result = result
        self.error = error
        self.calls = 0

    async def ensure_current(self) -> CorpusSyncResult:
        self.events.append("synchronize")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def access_context() -> DocumentAccessContext:
    now = datetime.now(UTC)
    return DocumentAccessContext(
        user_id=uuid4(),
        operations=frozenset({"documents.read"}),
        agent_id="informacoes",
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=1),
        token_id="token-1",
        request_id="request-1",
    )


def sync_result(*, updated: bool) -> CorpusSyncResult:
    return CorpusSyncResult(
        corpus_revision="a" * 64,
        changed_document_ids=(uuid4(),) if updated else (),
        removed_document_ids=(),
        checked_at=datetime.now(UTC),
        updated=updated,
    )


def use_case(events: list[str], sync: Synchronizer, *, allowed: bool = True):
    policy = DocumentAccessPolicy(PermissionDirectory(events, allowed=allowed))
    return EnsureCurrentCorpus(policy, sync, clock=lambda: datetime.now(UTC))


def run_immediate(coroutine):
    """Executa a cadeia de corrotinas dos fakes, que não faz I/O nem suspende."""

    iterator = coroutine.__await__()
    while True:
        try:
            yielded = iterator.send(None)
        except StopIteration as completed:
            return completed.value
        if yielded is not None:
            raise AssertionError("fake de unidade não deveria suspender a corrotina")


def test_permission_is_revalidated_before_synchronizer_is_called() -> None:
    events: list[str] = []
    sync = Synchronizer(events, result=sync_result(updated=True))

    outcome = run_immediate(use_case(events, sync).execute(access_context()))

    assert events == ["permission", "synchronize"]
    assert outcome.status == CorpusRefreshStatus.UPDATED
    assert outcome.corpus_revision == "a" * 64
    assert len(outcome.changed_document_ids) == 1


def test_missing_context_denies_without_reading_or_updating_the_corpus() -> None:
    events: list[str] = []
    sync = Synchronizer(events, result=sync_result(updated=False))

    with pytest.raises(AccessContextError):
        run_immediate(use_case(events, sync).execute(None))

    assert not events
    assert sync.calls == 0


def test_revoked_permission_denies_before_synchronization() -> None:
    events: list[str] = []
    sync = Synchronizer(events, result=sync_result(updated=False))

    with pytest.raises(AccessDeniedError):
        run_immediate(use_case(events, sync, allowed=False).execute(access_context()))

    assert events == ["permission"]
    assert sync.calls == 0


def test_unchanged_corpus_preserves_current_revision_and_reports_no_changes() -> None:
    events: list[str] = []
    sync = Synchronizer(events, result=sync_result(updated=False))

    outcome = run_immediate(use_case(events, sync).execute(access_context()))

    assert outcome.status == CorpusRefreshStatus.UNCHANGED
    assert outcome.corpus_revision == "a" * 64
    assert outcome.changed_document_ids == ()
    assert outcome.removed_document_ids == ()


def test_sync_failure_returns_unavailable_without_leaking_previous_revision() -> None:
    events: list[str] = []
    sync = Synchronizer(events, error=SourceReadError("detalhe interno"))

    outcome = run_immediate(use_case(events, sync).execute(access_context()))

    assert outcome.status == CorpusRefreshStatus.UNAVAILABLE
    assert outcome.corpus_revision is None
    assert outcome.changed_document_ids == ()
    assert outcome.removed_document_ids == ()


def test_concurrent_sync_returns_updating_without_exposing_old_revision() -> None:
    events: list[str] = []
    sync = Synchronizer(events, error=CorpusUpdatingError("já existe atualização"))

    outcome = run_immediate(use_case(events, sync).execute(access_context()))

    assert outcome.status == CorpusRefreshStatus.UPDATING
    assert outcome.corpus_revision is None


def test_incomplete_outcome_cannot_include_a_previous_revision_or_partial_changes() -> None:
    with pytest.raises(ValueError, match="revisão anterior"):
        EnsureCurrentCorpusOutcome(
            status=CorpusRefreshStatus.UNAVAILABLE,
            checked_at=datetime.now(UTC),
            corpus_revision="b" * 64,
        )
    with pytest.raises(ValueError, match="mudanças parciais"):
        EnsureCurrentCorpusOutcome(
            status=CorpusRefreshStatus.UPDATING,
            checked_at=datetime.now(UTC),
            changed_document_ids=(uuid4(),),
        )
