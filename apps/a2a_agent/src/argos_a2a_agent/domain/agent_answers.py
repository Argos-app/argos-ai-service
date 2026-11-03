"""Resultados internos dos especialistas e do provedor de respostas."""

import re
from dataclasses import dataclass

from argos_a2a_agent.domain.errors import DocumentAnswerGenerationError
from packages.contracts.a2a import (
    DocumentAnswerStatus,
    DocumentSkill,
    DocumentSourceReference,
)

_GENERATED_STATUSES = {
    DocumentAnswerStatus.SUCCESS,
    DocumentAnswerStatus.PARTIAL,
    DocumentAnswerStatus.NEEDS_CLARIFICATION,
    DocumentAnswerStatus.ABSTAIN,
}
_EVIDENCE_ID = re.compile(r"^ev_[0-9a-f]{24}$")


@dataclass(frozen=True, slots=True)
class AgentEvidence:
    evidence_id: str
    source: DocumentSourceReference

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not _EVIDENCE_ID.fullmatch(self.evidence_id):
            raise DocumentAnswerGenerationError("identificador interno de evidência inválido")


@dataclass(frozen=True, slots=True)
class GeneratedDocumentAnswer:
    answer: str
    status: DocumentAnswerStatus
    cited_evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status, DocumentAnswerStatus)
            or self.status not in _GENERATED_STATUSES
        ):
            raise DocumentAnswerGenerationError("o modelo não pode definir estado operacional")
        if not isinstance(self.answer, str) or len(self.answer) > 8_000:
            raise DocumentAnswerGenerationError("resposta gerada inválida ou extensa")
        if not isinstance(self.cited_evidence_ids, tuple) or any(
            not isinstance(item, str) or not _EVIDENCE_ID.fullmatch(item)
            for item in self.cited_evidence_ids
        ):
            raise DocumentAnswerGenerationError("identificador de evidência inválido")
        if len(self.cited_evidence_ids) > 20 or len(set(self.cited_evidence_ids)) != len(
            self.cited_evidence_ids
        ):
            raise DocumentAnswerGenerationError("lista de evidências geradas é inválida")
        if (
            not isinstance(self.limitations, tuple)
            or not isinstance(self.missing_fields, tuple)
            or len(self.limitations) > 20
            or len(self.missing_fields) > 20
            or any(not isinstance(item, str) or not item for item in self.limitations)
            or any(not isinstance(item, str) or not item for item in self.missing_fields)
        ):
            raise DocumentAnswerGenerationError("metadados da resposta excedem os limites")
        if self.status in {DocumentAnswerStatus.SUCCESS, DocumentAnswerStatus.PARTIAL}:
            if not self.answer.strip() or not self.cited_evidence_ids:
                raise DocumentAnswerGenerationError(
                    "resposta afirmativa exige texto e evidências citadas"
                )
        if self.status == DocumentAnswerStatus.PARTIAL and not self.limitations:
            raise DocumentAnswerGenerationError("resposta parcial exige limitações explícitas")
        if self.status == DocumentAnswerStatus.NEEDS_CLARIFICATION and not self.missing_fields:
            raise DocumentAnswerGenerationError(
                "pedido de esclarecimento exige indicar os campos ausentes"
            )


@dataclass(frozen=True, slots=True)
class SpecialistAnswerSummary:
    skill: DocumentSkill
    status: DocumentAnswerStatus
    answer: str
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    missing_fields: tuple[str, ...]
