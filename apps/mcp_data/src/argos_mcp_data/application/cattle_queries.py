"""Casos de consulta detalhada para o agente Fazenda."""

from datetime import UTC, datetime
from uuid import UUID

from argos_mcp_data.application.access_policy import AccessPolicy
from argos_mcp_data.domain.access import AccessContext, AuthorizedScope
from argos_mcp_data.domain.cattle import (
    AnimalLotHistoryRecord,
    ApplicationAnimalRecord,
    ApplicationRecord,
    ExceptionAnimalRecord,
    GetLotReportSourceRequest,
    HerdRecord,
    ListAnimalLotHistoryRequest,
    ListApplicationAnimalsRequest,
    ListApplicationsRequest,
    ListExceptionAnimalsRequest,
    ListHerdsRequest,
    ListLotsRequest,
    ListOccurrencesRequest,
    ListPropertiesRequest,
    LotRecord,
    OccurrenceRecord,
    Page,
    PropertyRecord,
)
from argos_mcp_data.domain.cattle_aggregates import (
    AggregatePage,
    ExceptionSummaryRequest,
    FarmSummaryRequest,
)
from argos_mcp_data.domain.cattle_documents import (
    CattleDocumentsPage,
    ListCattleDocumentsRequest,
)
from argos_mcp_data.domain.errors import AccessDeniedError
from argos_mcp_data.ports.cattle_aggregate_repository import CattleAggregateReadRepository
from argos_mcp_data.ports.cattle_document_repository import CattleDocumentReadRepository
from argos_mcp_data.ports.cattle_repository import CattleReadRepository
from argos_mcp_data.ports.report_signer import LotReportSigner
from packages.contracts import (
    AnimalExceptionRecord as ReportAnimalExceptionRecord,
)
from packages.contracts import (
    ApplicationRecord as ReportApplicationRecord,
)
from packages.contracts import (
    LotReportData,
    LotReportEnvelope,
    LotReportIdentity,
)


class CattleQueryService:
    """Autoriza leituras detalhadas e agregadas antes de consultar as fontes."""

    def __init__(
        self,
        access_policy: AccessPolicy,
        repository: CattleReadRepository,
        document_repository: CattleDocumentReadRepository,
        aggregate_repository: CattleAggregateReadRepository | None = None,
        report_signer: LotReportSigner | None = None,
    ) -> None:
        self._access_policy = access_policy
        self._repository = repository
        self._document_repository = document_repository
        self._aggregate_repository = aggregate_repository
        self._report_signer = report_signer

    async def _authorized_scope(
        self,
        context: AccessContext,
        selected_property_ids: tuple[UUID, ...] | None,
    ) -> AuthorizedScope:
        if context.agent_id != "fazenda":
            raise AccessDeniedError("ferramenta disponível somente ao agente Fazenda")
        return await self._access_policy.authorize(
            context,
            operation="cattle.read",
            selected_property_ids=selected_property_ids,
        )

    async def list_properties(
        self, context: AccessContext, request: ListPropertiesRequest
    ) -> Page[PropertyRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_properties(scope, request)

    async def list_herds(
        self, context: AccessContext, request: ListHerdsRequest
    ) -> Page[HerdRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_herds(scope, request)

    async def list_lots(self, context: AccessContext, request: ListLotsRequest) -> Page[LotRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_lots(scope, request)

    async def get_lot_report_source(
        self, context: AccessContext, request: GetLotReportSourceRequest
    ) -> LotReportEnvelope:
        if context.agent_id != "fazenda":
            raise AccessDeniedError("ferramenta disponível somente ao agente Fazenda")
        if self._report_signer is None:
            raise RuntimeError("assinatura de origem de relatório não está configurada")
        scope = await self._access_policy.authorize(
            context,
            operation="report.read",
            selected_property_ids=(request.property_id,),
        )
        property_scope = (request.property_id,)
        lot_page = await self._repository.list_lots(
            scope,
            ListLotsRequest(
                property_ids=property_scope,
                lot_id=request.lot_id,
                page_size=1,
            ),
        )
        lots = [item for item in lot_page.items if item.lot_id == request.lot_id]
        if len(lots) != 1 or lot_page.has_more or lots[0].property_id != request.property_id:
            raise AccessDeniedError("lote não encontrado no escopo autorizado")
        lot = lots[0]

        applications_page = await self._repository.list_applications(
            scope,
            ListApplicationsRequest(
                property_ids=property_scope,
                lot_id=request.lot_id,
                active=True,
                page_size=100,
            ),
        )
        exceptions_page = await self._repository.list_exception_animals(
            scope,
            ListExceptionAnimalsRequest(
                property_ids=property_scope,
                lot_id=request.lot_id,
                page_size=100,
            ),
        )
        if applications_page.has_more or exceptions_page.has_more:
            raise RuntimeError("relatório excede o limite completo de itens; nada foi assinado")
        if any(item.property_id != request.property_id for item in applications_page.items):
            raise AccessDeniedError("aplicação retornada fora da propriedade autorizada")
        if any(item.property_id != request.property_id for item in exceptions_page.items):
            raise AccessDeniedError("animal retornado fora da propriedade autorizada")

        queried_at = datetime.now(UTC)
        report = LotReportData(
            requested_lot=LotReportIdentity(
                lot_id=lot.lot_id,
                lot_code=lot.lot_code,
                lot_category=lot.category,
                lot_active=lot.active,
                lot_is_exception=lot.is_exception_lot,
                lot_exception_reason=lot.exception_reason,
                lot_head_count=lot.head_count,
                lot_open_date=lot.open_date,
                lot_close_date=lot.close_date,
                slaughter_release_date=lot.slaughter_release_date,
                fit_for_slaughter=lot.fit_for_slaughter,
                herd_id=lot.herd_id,
                herd_name=lot.herd_name,
                herd_breed=lot.herd_breed,
                property_id=lot.property_id,
                property_name=lot.property_name,
            ),
            applications=tuple(
                ReportApplicationRecord(
                    application_id=item.application_id,
                    application_kind=item.application_kind,
                    application_date=item.application_date,
                    application_time=item.application_time,
                    medicine_name=item.medicine_name,
                    active_ingredient=item.active_ingredient,
                    medicine_batch_number=item.medicine_batch_number,
                    dose=item.dose,
                    unit=item.unit,
                    route=item.route,
                    head_count_applied=item.head_count_applied,
                    meat_withdrawal_end=item.meat_withdrawal_end,
                    active=item.active,
                    goal=item.goal,
                )
                for item in applications_page.items
            ),
            exception_animals=tuple(
                ReportAnimalExceptionRecord(
                    animal_id=item.animal_id,
                    ear_tag=item.ear_tag,
                    category=item.category,
                    status=item.status,
                    birth_date=item.birth_date,
                    current_weight=item.current_weight,
                    exception_date=item.exception_date,
                    exception_end_date=item.exception_end_date,
                    exception_reason=item.exception_reason,
                    slaughter_release_date=item.slaughter_release_date,
                    fit_for_slaughter=item.fit_for_slaughter,
                )
                for item in exceptions_page.items
            ),
            queried_at=queried_at,
            data_as_of=queried_at,
        )
        return self._report_signer.sign(report, scope)

    async def list_exception_animals(
        self, context: AccessContext, request: ListExceptionAnimalsRequest
    ) -> Page[ExceptionAnimalRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_exception_animals(scope, request)

    async def list_applications(
        self, context: AccessContext, request: ListApplicationsRequest
    ) -> Page[ApplicationRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_applications(scope, request)

    async def list_application_animals(
        self, context: AccessContext, request: ListApplicationAnimalsRequest
    ) -> Page[ApplicationAnimalRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_application_animals(scope, request)

    async def list_occurrences(
        self, context: AccessContext, request: ListOccurrencesRequest
    ) -> Page[OccurrenceRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_occurrences(scope, request)

    async def list_animal_lot_history(
        self, context: AccessContext, request: ListAnimalLotHistoryRequest
    ) -> Page[AnimalLotHistoryRecord]:
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._repository.list_animal_lot_history(scope, request)

    async def list_cattle_documents(
        self, context: AccessContext, request: ListCattleDocumentsRequest
    ) -> CattleDocumentsPage:
        """Autoriza a consulta e retorna documentos Mongo do escopo efetivo."""
        scope = await self._authorized_scope(context, request.property_ids)
        return await self._document_repository.list_documents(scope, request)

    async def get_farm_summary(
        self, context: AccessContext, request: FarmSummaryRequest
    ) -> AggregatePage:
        scope = await self._authorized_scope(context, request.property_ids)
        if self._aggregate_repository is None:
            raise AccessDeniedError("consultas agregadas não estão configuradas")
        return await self._aggregate_repository.farm_summary(scope, request)

    async def summarize_exception_animals(
        self, context: AccessContext, request: ExceptionSummaryRequest
    ) -> AggregatePage:
        scope = await self._authorized_scope(context, request.property_ids)
        if self._aggregate_repository is None:
            raise AccessDeniedError("consultas agregadas não estão configuradas")
        return await self._aggregate_repository.exception_summary(scope, request)
