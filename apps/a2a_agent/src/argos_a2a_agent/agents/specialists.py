"""Agentes Documental e Legal com busca e evidência segregadas por tipo."""

import asyncio
import hashlib
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from langsmith import traceable

from argos_a2a_agent.adapters.observability.recording import observe_operation
from argos_a2a_agent.agents.prompts import (
    DOCUMENTAL_SYSTEM_PROMPT,
    LEGAL_SYSTEM_PROMPT,
    MIXED_SYSTEM_PROMPT,
)
from argos_a2a_agent.application.document_queries import SearchDocuments
from argos_a2a_agent.application.ensure_current_corpus import (
    EnsureCurrentCorpus,
    EnsureCurrentCorpusOutcome,
)
from argos_a2a_agent.domain.agent_answers import (
    AgentEvidence,
    GeneratedDocumentAnswer,
    SpecialistAnswerSummary,
)
from argos_a2a_agent.domain.errors import (
    CorpusSynchronizationError,
    CorpusUpdatingError,
    DocumentAgentDeadlineError,
    DocumentAnswerGenerationError,
    DocumentDomainError,
)
from argos_a2a_agent.ports.document_answer_generator import DocumentAnswerGenerator
from argos_a2a_agent.ports.document_read_repository import DocumentReadRepository
from packages.contracts.a2a import (
    CorpusRefreshStatus,
    DocumentAnswerResponse,
    DocumentAnswerStatus,
    DocumentEvidenceRequirement,
    DocumentQuestionRequest,
    DocumentSkill,
    DocumentSourceReference,
    DocumentType,
)
from packages.contracts.documents import SearchDocumentsRequest
from packages.observability import (
    ErrorOrigin,
    ObservationCollector,
    ObservationLevel,
    OperationKind,
    OperationPhase,
    ResultMetadata,
)
from packages.security.access import DocumentAccessContext

Clock = Callable[[], datetime]
_CITATION_MARKER = re.compile(r"\[\[(ev_[0-9a-f]{24})\]\]")
_MAX_FALLBACK_EVIDENCE_ITEMS = 3
_MAX_FALLBACK_EVIDENCE_CHARS = 6_000


class _DocumentSpecialist:
    skill: DocumentSkill
    document_type: DocumentType
    system_prompt: str

    def __init__(
        self,
        search: SearchDocuments,
        ensure_current: EnsureCurrentCorpus,
        repository: DocumentReadRepository,
        answer_generator: DocumentAnswerGenerator,
        *,
        clock: Clock = lambda: datetime.now(UTC),
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        self._search = search
        self._ensure_current = ensure_current
        self._repository = repository
        self._answer_generator = answer_generator
        self._clock = clock
        self._observation_collector = observation_collector

    @traceable(name="a2a.agent.specialist", run_type="chain")
    async def execute(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
    ) -> DocumentAnswerResponse:
        async with observe_operation(
            self._observation_collector,
            operation_kind=OperationKind.AGENT,
            operation_name=f"a2a.agent.{self.skill.value}",
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.LOCAL_OPERATION,
            agent=self.skill.value,
            error_origin=ErrorOrigin.APPLICATION,
        ) as recorder:
            response = await self._execute(request, context)
            if recorder is not None:
                recorder.finish(
                    result=ResultMetadata(
                        source="document_answer",
                        code=response.status.value,
                    )
                )
            return response

    async def _execute(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
    ) -> DocumentAnswerResponse:
        if request.skill not in {self.skill, DocumentSkill.MIXED}:
            raise ValueError("a skill solicitada não corresponde ao agente")
        self._check_deadline(request)

        if self.document_type not in (request.document_types or (self.document_type,)):
            current = await self._current(context)
            return await self._static_response(
                request,
                context,
                current.corpus_revision,
                DocumentAnswerStatus.ABSTAIN,
                limitations=(
                    f"A consulta não incluiu documentos do tipo {self.document_type.value}.",
                ),
            )

        current = await self._current(context) if self._needs_early_answer(request) else None
        if self.skill == DocumentSkill.LEGAL:
            if request.jurisdiction is None:
                current = current or await self._current(context)
                return await self._static_response(
                    request,
                    context,
                    current.corpus_revision,
                    DocumentAnswerStatus.NEEDS_CLARIFICATION,
                    answer="Informe a jurisdição para que eu possa consultar as leis pertinentes.",
                    missing_fields=("jurisdiction",),
                )
            if (
                request.reference_date is not None
                and request.reference_date != self._clock().date()
            ):
                current = current or await self._current(context)
                return await self._static_response(
                    request,
                    context,
                    current.corpus_revision,
                    DocumentAnswerStatus.ABSTAIN,
                    limitations=(
                        "O corpus contém apenas a revisão atual e não sustenta consultas "
                        "históricas ou futuras.",
                    ),
                )

        search_response = await self._search.execute(
            self._search_request(request),
            context,
        )
        evidence = tuple(
            _evidence(source)
            for source in search_response.hits
            if self._satisfies_requirements(source, request.evidence_requirements)
        )
        missing_fields = self._missing_required_fields(
            search_response.hits,
            evidence,
            request.evidence_requirements,
        )
        if missing_fields:
            return await self._static_response(
                request,
                context,
                search_response.corpus_revision,
                DocumentAnswerStatus.NEEDS_CLARIFICATION,
                answer="Os metadados necessários para responder não estão disponíveis.",
                missing_fields=missing_fields,
            )
        if not evidence:
            return await self._static_response(
                request,
                context,
                search_response.corpus_revision,
                DocumentAnswerStatus.ABSTAIN,
                limitations=("Nenhuma evidência atual e compatível foi encontrada.",),
            )

        try:
            draft = await self._generate(
                request,
                system_prompt=self.system_prompt,
                evidence=evidence,
            )
        except DocumentAnswerGenerationError:
            draft = _uninterpreted_evidence_answer(evidence)
        if draft.status == DocumentAnswerStatus.ABSTAIN:
            draft = _uninterpreted_evidence_answer(evidence) or draft
        return await self._response_from_draft(
            request,
            context,
            search_response.corpus_revision,
            evidence,
            draft,
        )

    def _search_request(self, request: DocumentQuestionRequest) -> SearchDocumentsRequest:
        return SearchDocumentsRequest(
            query=request.question,
            top_k=8,
            document_ids=request.document_ids,
            document_types=(self.document_type,),
            jurisdictions=(request.jurisdiction,)
            if request.jurisdiction and self.skill == DocumentSkill.LEGAL
            else None,
        )

    def _needs_early_answer(self, request: DocumentQuestionRequest) -> bool:
        return self.skill == DocumentSkill.LEGAL and (
            request.jurisdiction is None
            or request.reference_date is not None
            and request.reference_date != self._clock().date()
        )

    @staticmethod
    def _satisfies_requirements(
        source: DocumentSourceReference,
        requirements: Sequence[DocumentEvidenceRequirement],
    ) -> bool:
        if DocumentEvidenceRequirement.JURISDICTION in requirements and source.jurisdiction is None:
            return False
        if (
            DocumentEvidenceRequirement.DOCUMENT_DATE in requirements
            and source.document_date is None
        ):
            return False
        return bool(
            source.document_id
            and source.page_from >= 1
            and source.page_to >= source.page_from
            and source.excerpt.strip()
        )

    @staticmethod
    def _missing_required_fields(
        found: Sequence[DocumentSourceReference],
        accepted: Sequence[AgentEvidence],
        requirements: Sequence[DocumentEvidenceRequirement],
    ) -> tuple[str, ...]:
        if not found or accepted:
            return ()
        missing = []
        if DocumentEvidenceRequirement.JURISDICTION in requirements:
            if any(source.jurisdiction is None for source in found):
                missing.append("jurisdiction")
        if DocumentEvidenceRequirement.DOCUMENT_DATE in requirements:
            if any(source.document_date is None for source in found):
                missing.append("document_date")
        return tuple(missing)

    async def _generate(
        self,
        request: DocumentQuestionRequest,
        *,
        system_prompt: str,
        evidence: Sequence[AgentEvidence],
        specialist_answers: Sequence[SpecialistAnswerSummary] = (),
    ) -> GeneratedDocumentAnswer:
        remaining = self._remaining_seconds(request)
        try:
            draft = await asyncio.wait_for(
                self._answer_generator.generate(
                    system_prompt=system_prompt,
                    question=request.question,
                    jurisdiction=request.jurisdiction,
                    reference_date=request.reference_date,
                    evidence=evidence,
                    specialist_answers=specialist_answers,
                ),
                timeout=remaining,
            )
        except TimeoutError as exc:
            raise DocumentAgentDeadlineError("o prazo expirou durante a geração") from exc
        except DocumentDomainError:
            raise
        except Exception as exc:
            raise DocumentAnswerGenerationError("não foi possível gerar a resposta") from exc
        self._check_deadline(request)
        self._validate_citations(draft, evidence)
        return draft

    async def _response_from_draft(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
        revision: str,
        evidence: Sequence[AgentEvidence],
        draft: GeneratedDocumentAnswer,
    ) -> DocumentAnswerResponse:
        current = await self._current(context, expected_revision=revision)
        self._check_deadline(request)
        selected = {item.evidence_id: item.source for item in evidence}
        sources = tuple(selected[item] for item in draft.cited_evidence_ids)
        answer = draft.answer
        for index, evidence_id in enumerate(draft.cited_evidence_ids, start=1):
            answer = answer.replace(f"[[{evidence_id}]]", f"[{index}]")
        return DocumentAnswerResponse(
            request_id=request.request_id,
            status=draft.status,
            answer=answer,
            sources=sources,
            limitations=draft.limitations,
            missing_fields=draft.missing_fields,
            corpus_revision=revision,
            checked_at=current.checked_at,
            refresh_status=current.status,
        )

    async def _static_response(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
        revision: str,
        status: DocumentAnswerStatus,
        *,
        answer: str = "",
        limitations: tuple[str, ...] = (),
        missing_fields: tuple[str, ...] = (),
    ) -> DocumentAnswerResponse:
        self._check_deadline(request)
        current = await self._current(context, expected_revision=revision)
        self._check_deadline(request)
        return DocumentAnswerResponse(
            request_id=request.request_id,
            status=status,
            answer=answer,
            limitations=limitations,
            missing_fields=missing_fields,
            corpus_revision=revision,
            checked_at=current.checked_at,
            refresh_status=current.status,
        )

    async def _current(
        self,
        context: DocumentAccessContext | None,
        *,
        expected_revision: str | None = None,
    ) -> EnsureCurrentCorpusOutcome:
        outcome = await self._ensure_current.execute(context)
        if outcome.status == CorpusRefreshStatus.UPDATING:
            raise CorpusUpdatingError("o corpus documental está sendo atualizado")
        if outcome.status == CorpusRefreshStatus.UNAVAILABLE:
            raise CorpusSynchronizationError("não foi possível confirmar o corpus atual")
        if outcome.corpus_revision is None:
            raise CorpusSynchronizationError("a sincronização não confirmou uma revisão")
        if expected_revision is not None and outcome.corpus_revision != expected_revision:
            raise CorpusUpdatingError("o corpus mudou durante a geração; consulte novamente")
        return outcome

    def _check_deadline(self, request: DocumentQuestionRequest) -> None:
        self._remaining_seconds(request)

    def _remaining_seconds(self, request: DocumentQuestionRequest) -> float:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock do agente deve produzir timestamp com timezone")
        remaining = (request.deadline_at - now).total_seconds()
        if remaining <= 0:
            raise DocumentAgentDeadlineError("o prazo da pergunta expirou")
        return remaining

    @staticmethod
    def _validate_citations(
        draft: GeneratedDocumentAnswer,
        evidence: Sequence[AgentEvidence],
    ) -> None:
        allowed = {item.evidence_id for item in evidence}
        if not set(draft.cited_evidence_ids) <= allowed:
            raise DocumentAnswerGenerationError("a resposta citou evidência que não foi recuperada")
        if draft.status in {DocumentAnswerStatus.SUCCESS, DocumentAnswerStatus.PARTIAL}:
            inline_citations = set(_CITATION_MARKER.findall(draft.answer))
            if inline_citations != set(draft.cited_evidence_ids):
                raise DocumentAnswerGenerationError(
                    "citações no texto devem corresponder às evidências estruturadas"
                )


class DocumentalAgent(_DocumentSpecialist):
    skill = DocumentSkill.DOCUMENTAL
    document_type = DocumentType.TERMS_CONDITIONS
    system_prompt = DOCUMENTAL_SYSTEM_PROMPT


class LegalAgent(_DocumentSpecialist):
    skill = DocumentSkill.LEGAL
    document_type = DocumentType.LAW
    system_prompt = LEGAL_SYSTEM_PROMPT


class MixedDocumentAgent(_DocumentSpecialist):
    """Executa os dois especialistas e compara resultados sem resolver precedência."""

    skill = DocumentSkill.MIXED
    document_type = DocumentType.LAW
    system_prompt = MIXED_SYSTEM_PROMPT

    def __init__(
        self,
        search: SearchDocuments,
        ensure_current: EnsureCurrentCorpus,
        repository: DocumentReadRepository,
        answer_generator: DocumentAnswerGenerator,
        documental: DocumentalAgent,
        legal: LegalAgent,
        *,
        clock: Clock = lambda: datetime.now(UTC),
        observation_collector: ObservationCollector | None = None,
    ) -> None:
        super().__init__(
            search,
            ensure_current,
            repository,
            answer_generator,
            clock=clock,
            observation_collector=observation_collector,
        )
        self._documental = documental
        self._legal = legal

    @traceable(name="a2a.agent.mixed", run_type="chain")
    async def execute(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
    ) -> DocumentAnswerResponse:
        async with observe_operation(
            self._observation_collector,
            operation_kind=OperationKind.AGENT,
            operation_name=f"a2a.agent.{self.skill.value}",
            operation_phase=OperationPhase.EXECUTION,
            observation_level=ObservationLevel.LOCAL_OPERATION,
            agent=self.skill.value,
            error_origin=ErrorOrigin.APPLICATION,
        ) as recorder:
            response = await self._execute(request, context)
            if recorder is not None:
                recorder.finish(
                    result=ResultMetadata(
                        source="document_answer",
                        code=response.status.value,
                    )
                )
            return response

    async def _execute(
        self,
        request: DocumentQuestionRequest,
        context: DocumentAccessContext | None,
    ) -> DocumentAnswerResponse:
        """O sincronizador serializa acessos e devolve updating a outra chamada;
        execução sequencial evita que este próprio coordenador provoque esse estado."""
        if request.skill != DocumentSkill.MIXED:
            raise ValueError("o agente misto exige skill=mixed")
        self._check_deadline(request)
        if request.jurisdiction is None:
            current = await self._current(context)
            return await self._static_response(
                request,
                context,
                current.corpus_revision,
                DocumentAnswerStatus.NEEDS_CLARIFICATION,
                answer="Informe a jurisdição para que eu compare os termos às leis aplicáveis.",
                missing_fields=("jurisdiction",),
            )
        if request.reference_date is not None and request.reference_date != self._clock().date():
            current = await self._current(context)
            return await self._static_response(
                request,
                context,
                current.corpus_revision,
                DocumentAnswerStatus.ABSTAIN,
                limitations=(
                    "O corpus contém apenas a revisão atual e não sustenta consultas "
                    "históricas ou futuras.",
                ),
            )

        documental_answer = await self._documental.execute(request, context)
        legal_answer = await self._legal.execute(request, context)
        if documental_answer.corpus_revision != legal_answer.corpus_revision:
            raise CorpusUpdatingError("o corpus mudou entre as consultas dos especialistas")
        revision = documental_answer.corpus_revision
        if revision is None:
            raise CorpusSynchronizationError("os especialistas não confirmaram a revisão")

        clarifications = tuple(
            dict.fromkeys(
                field
                for response in (documental_answer, legal_answer)
                if response.status == DocumentAnswerStatus.NEEDS_CLARIFICATION
                for field in response.missing_fields
            )
        )
        if clarifications:
            return await self._static_response(
                request,
                context,
                revision,
                DocumentAnswerStatus.NEEDS_CLARIFICATION,
                answer="São necessários dados adicionais para comparar as fontes com segurança.",
                missing_fields=clarifications,
            )

        evidence_by_id: dict[str, AgentEvidence] = {}
        summaries = []
        for skill, response in (
            (DocumentSkill.DOCUMENTAL, documental_answer),
            (DocumentSkill.LEGAL, legal_answer),
        ):
            source_evidence = tuple(_evidence(source) for source in response.sources)
            evidence_by_id.update((item.evidence_id, item) for item in source_evidence)
            summaries.append(
                SpecialistAnswerSummary(
                    skill=skill,
                    status=response.status,
                    answer=response.answer,
                    evidence_ids=tuple(item.evidence_id for item in source_evidence),
                    limitations=response.limitations,
                    missing_fields=response.missing_fields,
                )
            )
        evidence = tuple(evidence_by_id.values())
        if not evidence:
            limitations = tuple(
                dict.fromkeys(
                    limitation
                    for response in (documental_answer, legal_answer)
                    for limitation in response.limitations
                )
            ) or ("Nenhuma evidência atual foi encontrada nos termos ou nas leis.",)
            return await self._static_response(
                request,
                context,
                revision,
                DocumentAnswerStatus.ABSTAIN,
                limitations=limitations,
            )

        try:
            draft = await self._generate(
                request,
                system_prompt=self.system_prompt,
                evidence=evidence,
                specialist_answers=tuple(summaries),
            )
        except DocumentAnswerGenerationError:
            draft = _uninterpreted_evidence_answer(evidence)
        if draft.status == DocumentAnswerStatus.ABSTAIN:
            draft = _uninterpreted_evidence_answer(evidence) or draft
        return await self._response_from_draft(
            request,
            context,
            revision,
            evidence,
            draft,
        )


def _evidence(source: DocumentSourceReference) -> AgentEvidence:
    material = "|".join(
        (
            str(source.document_id),
            source.revision_hash,
            str(source.page_from),
            str(source.page_to),
            source.excerpt,
        )
    )
    evidence_id = "ev_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return AgentEvidence(evidence_id=evidence_id, source=source)


def _uninterpreted_evidence_answer(
    evidence: Sequence[AgentEvidence],
) -> GeneratedDocumentAnswer | None:
    """Preserva os trechos recuperados quando o provider não retorna uma síntese com citações."""
    selected = evidence[:_MAX_FALLBACK_EVIDENCE_ITEMS]
    blocks: list[str] = []
    citations: list[str] = []
    remaining = _MAX_FALLBACK_EVIDENCE_CHARS
    for item in selected:
        source = item.source
        excerpt = source.excerpt.strip()
        if not excerpt or remaining <= 0:
            continue
        page_label = (
            f"p. {source.page_from}"
            if source.page_from == source.page_to
            else f"pp. {source.page_from}–{source.page_to}"
        )
        prefix = f"{source.title} ({page_label}): "
        marker = f" [[{item.evidence_id}]]"
        block = f"{prefix}{excerpt[: max(0, remaining - len(prefix) - len(marker))]}{marker}"
        if len(block) <= len(prefix) + len(marker):
            break
        blocks.append(block)
        citations.append(item.evidence_id)
        remaining -= len(block)
    if not blocks:
        return None
    return GeneratedDocumentAnswer(
        answer=(
            "A síntese do modelo não foi validada. Estes são trechos recuperados diretamente "
            "do corpus, sem interpretação adicional:\n\n" + "\n\n".join(blocks)
        ),
        status=DocumentAnswerStatus.PARTIAL,
        cited_evidence_ids=tuple(citations),
        limitations=(
            "A geração estruturada falhou; os trechos citados são evidência textual, "
            "não uma resposta interpretada à pergunta.",
        ),
    )
