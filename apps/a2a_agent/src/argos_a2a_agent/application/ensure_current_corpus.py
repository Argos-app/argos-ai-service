"""Autoriza e verifica a atualidade do corpus antes de qualquer leitura."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from argos_a2a_agent.domain.errors import (
    CorpusUpdatingError,
    DocumentDomainError,
)
from argos_a2a_agent.domain.models import CorpusSyncResult, RevisionHash
from argos_a2a_agent.ports.corpus_synchronizer import CorpusSynchronizer
from packages.contracts.a2a import CorpusRefreshStatus
from packages.security.access import DocumentAccessContext
from packages.security.document_access import DocumentAccessPolicy

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class EnsureCurrentCorpusOutcome:
    status: CorpusRefreshStatus
    checked_at: datetime
    corpus_revision: RevisionHash | None = None
    changed_document_ids: tuple[UUID, ...] = ()
    removed_document_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("checked_at deve incluir timezone")
        if self.status in {CorpusRefreshStatus.UPDATED, CorpusRefreshStatus.UNCHANGED}:
            if not isinstance(self.corpus_revision, str) or not _SHA256.fullmatch(
                self.corpus_revision
            ):
                raise ValueError("estado concluído exige revisão SHA-256 válida")
        elif self.corpus_revision is not None:
            raise ValueError("estado updating/unavailable não pode expor revisão anterior")
        if self.status == CorpusRefreshStatus.UNCHANGED and (
            self.changed_document_ids or self.removed_document_ids
        ):
            raise ValueError("estado unchanged não pode indicar documentos alterados")
        if self.status in {CorpusRefreshStatus.UPDATING, CorpusRefreshStatus.UNAVAILABLE} and (
            self.changed_document_ids or self.removed_document_ids
        ):
            raise ValueError("estado incompleto não pode expor mudanças parciais")


class EnsureCurrentCorpus:
    """Bloqueia a atualização até confirmar acesso global vigente do usuário."""

    def __init__(
        self,
        access_policy: DocumentAccessPolicy,
        synchronizer: CorpusSynchronizer,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._access_policy = access_policy
        self._synchronizer = synchronizer
        self._clock = clock

    async def execute(self, context: DocumentAccessContext | None) -> EnsureCurrentCorpusOutcome:
        # A autorização acontece antes de chamar qualquer porta capaz de ler
        # arquivos, consultar manifesto ou escrever no índice.
        await self._access_policy.authorize(context)

        try:
            result = await self._synchronizer.ensure_current()
        except CorpusUpdatingError:
            return EnsureCurrentCorpusOutcome(
                status=CorpusRefreshStatus.UPDATING,
                checked_at=self._clock(),
            )
        except DocumentDomainError:
            return EnsureCurrentCorpusOutcome(
                status=CorpusRefreshStatus.UNAVAILABLE,
                checked_at=self._clock(),
            )

        return self._from_sync_result(result)

    @staticmethod
    def _from_sync_result(result: CorpusSyncResult) -> EnsureCurrentCorpusOutcome:
        status = CorpusRefreshStatus.UPDATED if result.updated else CorpusRefreshStatus.UNCHANGED
        return EnsureCurrentCorpusOutcome(
            status=status,
            corpus_revision=result.corpus_revision,
            checked_at=result.checked_at,
            changed_document_ids=result.changed_document_ids,
            removed_document_ids=result.removed_document_ids,
        )
